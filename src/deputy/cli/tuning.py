"""`deputy bench` measures the chosen model; `deputy tune` also recommends better settings."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable

from deputy.agent import Result
from deputy.bench import (
    Summary,
    Variant,
    candidate_variants,
    choose_best,
    confirms,
    holdout_names,
    run_suite,
    selection_names,
)
from deputy.cli._common import Subparsers
from deputy.config import Backend, Config, ConfigError, load_config
from deputy.tune import revert_tuned, save_model_profile, tuned_path, write_tuned

THINKING_HINT = (
    "Most of the time is the model thinking. Request-level settings did not help; if your server "
    "is llama.cpp, cap the thinking with --reasoning-budget N (see docs/local-serving.md)."
)
THINKING_TOKENS_THRESHOLD = 800


def _pass(r: Summary) -> str:
    spread = f"\u00b1{r.pass_rate_stdev * 100:.0f}" if r.pass_rate_stdev else ""
    return f"{r.pass_rate:.0%}{spread}"


_COLUMNS: tuple[tuple[str, int, Callable[[Summary], str]], ...] = (
    ("variant", -14, lambda r: r.variant),
    ("pass", 9, _pass),
    ("cites", 7, lambda r: f"{r.citation_rate:.0%}"),
    ("turns", 7, lambda r: f"{r.mean_turns:.1f}"),
    ("tok out", 9, lambda r: f"{r.mean_tokens_out:.0f}"),
    ("\u00b1tok", 6, lambda r: f"{r.stdev_tokens_out:.0f}"),
    ("sec", 8, lambda r: f"{r.mean_elapsed_s:.1f}"),
    ("\u00b1sec", 6, lambda r: f"{r.stdev_elapsed_s:.1f}"),
    ("tok/s", 7, lambda r: f"{r.tokens_per_s:.1f}"),
    ("saved*", 8, lambda r: f"{r.mean_saved_tokens_est:.0f}"),
)


def _cell(text: str, width: int) -> str:
    """Pad to |width|: left-aligned for a negative width, right-aligned otherwise."""
    return text.ljust(-width) if width < 0 else text.rjust(width)


def format_table(rows: list[Summary]) -> str:
    head = "".join(_cell(name, width) for name, width, _ in _COLUMNS)
    body = ("".join(_cell(get(row), width) for _, width, get in _COLUMNS) for row in rows)
    note = "* saved: estimated tokens the orchestrator did not have to read (chars / 4)"
    return "\n".join([head, *body, note])


def _progress(name: str, result: Result) -> None:
    print(
        f"  {name:<22} {result.status:<14} {result.metrics.elapsed_s:>6.1f}s "
        f"{result.metrics.tokens_out:>5} tok out, {result.metrics.turns} turns",
        file=sys.stderr,
        flush=True,
    )


def parse_variant(text: str) -> Variant:
    name, separator, raw = text.partition("=")
    if not separator:
        raise ConfigError('--variant needs NAME=JSON, e.g. cool={"extra_body":{"temperature":0.1}}')
    try:
        return Variant(name=name, **json.loads(raw))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"--variant {name}: {exc}") from exc


async def _measure(
    config: Config, args: argparse.Namespace, variants: list[Variant], only: list[str] | None
) -> tuple[Backend, list[Summary]]:
    backend = config.backend(args.backend)
    summaries: list[Summary] = []
    for variant in [Variant(name="baseline"), *variants]:
        print(f"== {variant.name}", file=sys.stderr, flush=True)
        summaries.append(
            await run_suite(
                backend,
                variant,
                runs=args.runs,
                only=only,
                run_code=args.run_code,
                slow=getattr(args, "slow", False),
                use_lessons=args.lessons,
                policy=config.policy,
                on_result=_progress,
            )
        )
    return backend, summaries


def bench(args: argparse.Namespace) -> int:
    try:
        raw: list[str] = args.variant or []
        extra = [parse_variant(v) for v in raw]
        only = args.tasks.split(",") if args.tasks else None
        backend, summaries = asyncio.run(_measure(load_config(), args, extra, only))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    profile = save_model_profile(backend, summaries[0], summaries[1:])
    if args.json:
        print(
            json.dumps({"summaries": [s.model_dump() for s in summaries], "profile": str(profile)})
        )
        return 0
    print(format_table(summaries))
    print(f"profile saved: {profile}")
    return 0


def _settle(config: Config, backend: Backend, variant: Variant, *, apply_now: bool) -> str:
    """Apply, propose or skip the winning variant, according to `auto_tune`."""
    if apply_now or config.auto_tune == "apply":
        path = write_tuned(backend.name, variant)
        return f"applied: wrote {path} (undo with: deputy tune --revert)"
    if config.auto_tune == "off":
        return "auto_tune is off: nothing changed."
    return f"to apply it: deputy tune --apply   (writes {tuned_path()})"


async def _confirm(
    config: Config, args: argparse.Namespace, winner: Variant, holdout: list[str]
) -> list[Summary]:
    """Baseline and winner on the held-out tasks, which the choice never saw."""
    backend = config.backend(args.backend)
    confirmed: list[Summary] = []
    for variant in (Variant(name="baseline"), winner):
        print(f"== held-out: {variant.name}", file=sys.stderr, flush=True)
        confirmed.append(
            await run_suite(
                backend,
                variant,
                runs=args.runs,
                only=holdout,
                run_code=args.run_code,
                use_lessons=args.lessons,
                policy=config.policy,
                on_result=_progress,
            )
        )
    return confirmed


def _tuning_tasks(args: argparse.Namespace) -> tuple[list[str], list[str]]:
    """The tasks the choice is made on, and those that only confirm it (narrowed by --tasks)."""
    wanted = args.tasks.split(",") if args.tasks else None
    keep = [name for name in selection_names() if not wanted or name in wanted]
    confirm = [name for name in holdout_names() if not wanted or name in wanted]
    return keep, confirm


def tune(args: argparse.Namespace) -> int:
    if args.revert:
        print(revert_tuned())
        return 0
    candidates = candidate_variants()
    selection, holdout = _tuning_tasks(args)
    if not selection:
        print("error: no tuning task left after --tasks", file=sys.stderr)
        return 1
    try:
        config = load_config()
        backend, summaries = asyncio.run(_measure(config, args, candidates, selection))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    baseline, measured = summaries[0], summaries[1:]
    print(format_table(summaries))
    print(f"profile saved: {save_model_profile(backend, baseline, measured)}")
    best = choose_best(baseline, measured)
    if best is None:
        print("\nrecommendation: keep your current settings (no candidate was clearly better).")
        if baseline.mean_tokens_out > THINKING_TOKENS_THRESHOLD:
            print(THINKING_HINT)
        return 0
    winner = next(v for v in candidates if v.name == best.variant)
    note = "not confirmed on held-out tasks (excluded by --tasks)"
    if holdout:
        held_baseline, held_winner = asyncio.run(_confirm(config, args, winner, holdout))
        print("\nheld-out confirmation (tasks the choice never saw)")
        print(format_table([held_baseline, held_winner]))
        if not confirms(held_baseline, held_winner):
            print(
                f"\nrecommendation withdrawn: {winner.name} did not hold up on the held-out tasks."
            )
            return 0
        note = "confirmed on held-out tasks"
    print(
        f"\nrecommendation: {winner.name} (same accuracy, "
        f"{best.mean_elapsed_s}s vs {baseline.mean_elapsed_s}s; {note})"
    )
    print(_settle(config, backend, winner, apply_now=args.apply))
    return 0


def register(sub: Subparsers) -> None:
    for name, help_text, func in (
        ("bench", "measure the chosen model on a built-in suite", bench),
        ("tune", "measure, then recommend (or apply) better settings", tune),
    ):
        parser = sub.add_parser(name, help=help_text)
        parser.add_argument("--backend", help="backend name (default: the default backend)")
        parser.add_argument("--runs", type=int, default=1, help="repetitions of the suite")
        parser.add_argument("--tasks", help="comma-separated task names")
        parser.add_argument(
            "--lessons",
            action="store_true",
            help="apply the learned lessons (by default the benchmark measures the raw model)",
        )
        parser.add_argument(
            "--run-code",
            action="store_true",
            help="also run tasks whose grader executes model-written code (in a temp directory)",
        )
        if name == "bench":
            parser.add_argument(
                "--slow",
                action="store_true",
                help="also run tasks that take minutes (a file larger than the context)",
            )
            parser.add_argument("--variant", action="append", help="extra setting: NAME=JSON")
            parser.add_argument("--json", action="store_true", help="print JSON, not a table")
        else:
            parser.add_argument("--apply", action="store_true", help="write the winner")
            parser.add_argument("--revert", action="store_true", help="undo the last tuning")
        parser.set_defaults(func=func)
