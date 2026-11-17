"""The `deputy` command."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from importlib import metadata
from pathlib import Path

from deputy import install as installer
from deputy import workspace
from deputy.agent import Result
from deputy.backends import ChatClient
from deputy.bench import Summary, Variant, candidate_variants, choose_best, run_suite
from deputy.config import (
    Backend,
    ConfigError,
    config_path,
    load_config,
    state_dir,
)
from deputy.profiles import load_profiles
from deputy.tune import revert_tuned, save_model_profile, tuned_path, write_tuned

CONFIG_TEMPLATE = """# deputy configuration. No secrets here: use a key file (mode 600) or env var.
default_backend = "{name}"

[[backends]]
name = "{name}"
base_url = "{url}"
model = "{model}"
kind = "local"
{key_line}
# [policy]
# allowed_commands = [["node", "--test"], ["python", "-m", "pytest"]]
"""


def _version() -> str:
    try:
        return metadata.version("deputy")
    except metadata.PackageNotFoundError:
        return "0+unknown"


def _ask(prompt: str, default: str, interactive: bool) -> str:
    if not interactive:
        return default
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def cmd_init(args: argparse.Namespace) -> int:
    target = config_path()
    if target.exists() and not args.force:
        print(f"{target} already exists; use --force to overwrite", file=sys.stderr)
        return 1
    interactive = sys.stdin.isatty() and not args.yes
    url = args.url or _ask("Model server URL", "http://127.0.0.1:8080", interactive)
    model = args.model or _ask("Model name", "default", interactive)
    key_file = args.key_file or _ask("API key file (blank for none)", "", interactive)
    key_line = f'api_key_file = "{key_file}"' if key_file else ""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        CONFIG_TEMPLATE.format(name=args.name, url=url, model=model, key_line=key_line),
        encoding="utf-8",
    )
    target.chmod(0o600)
    print(f"wrote {target}\nnext: deputy doctor")
    return 0


async def _doctor() -> int:
    print(f"deputy {_version()}  python {sys.version.split()[0]}")
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"config: FAIL - {exc}")
        return 1
    print(f"config: ok ({config_path()})")
    failed = 0
    for backend in config.backends:
        client = ChatClient(backend)
        try:
            ok, detail = await client.health()
        except ConfigError as exc:
            ok, detail = False, str(exc)
        finally:
            await client.aclose()
        marker = "ok" if ok else "FAIL"
        print(f"backend {backend.name}: {marker} - {detail}")
        failed += 0 if ok else 1
    print(f"profiles: {', '.join(load_profiles(Path.cwd()))}")
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        print(f"state: ok ({state_dir()})")
    except OSError as exc:
        print(f"state: FAIL - {exc.strerror}")
        failed += 1
    return 1 if failed else 0


def cmd_doctor(_: argparse.Namespace) -> int:
    return asyncio.run(_doctor())


def cmd_install(args: argparse.Namespace) -> int:
    print(installer.snippet(args.agent))
    if args.apply:
        try:
            print(installer.apply(args.agent, Path.cwd()))
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    else:
        print("\n(printed only; add --apply to do it for you)")
    return 0


def cmd_instructions(args: argparse.Namespace) -> int:
    if args.write:
        print(installer.write_instructions(Path(args.write)))
    else:
        print(installer.instructions_text())
    return 0


def _progress(name: str, result: Result) -> None:
    print(
        f"  {name:<22} {result.status:<14} {result.metrics.elapsed_s:>6.1f}s "
        f"{result.metrics.tokens_out:>5} tok out, {result.metrics.turns} turns",
        file=sys.stderr,
        flush=True,
    )


def _table(rows: list[Summary]) -> str:
    head = f"{'variant':<14}{'pass':>6}{'cites':>7}{'turns':>7}{'tok out':>9}{'sec':>8}{'+-':>6}{'tok/s':>7}"  # noqa: E501
    lines = [head]
    for r in rows:
        lines.append(
            f"{r.variant:<14}{r.pass_rate:>6.0%}{r.citation_rate:>7.0%}{r.mean_turns:>7.1f}"
            f"{r.mean_tokens_out:>9.0f}{r.mean_elapsed_s:>8.1f}{r.stdev_elapsed_s:>6.1f}{r.tokens_per_s:>7.1f}"
        )
    return "\n".join(lines)


def _parse_variant(text: str) -> Variant:
    name, sep, raw = text.partition("=")
    if not sep:
        raise ConfigError('--variant needs NAME=JSON, e.g. cool={"extra_body":{"temperature":0.1}}')
    try:
        return Variant(name=name, **json.loads(raw))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"--variant {name}: {exc}") from exc


async def _measure(
    args: argparse.Namespace, variants: list[Variant]
) -> tuple[Backend, list[Summary]]:
    config = load_config()
    backend = config.backend(args.backend)
    only = args.tasks.split(",") if args.tasks else None
    summaries: list[Summary] = []
    for variant in [Variant(name="baseline"), *variants]:
        print(f"== {variant.name}", file=sys.stderr, flush=True)
        summaries.append(
            await run_suite(
                backend,
                variant,
                runs=args.runs,
                only=only,
                policy=config.policy,
                on_result=_progress,
            )
        )
    return backend, summaries


def cmd_bench(args: argparse.Namespace) -> int:
    try:
        raw: list[str] = args.variant or []
        extra = [_parse_variant(v) for v in raw]
        backend, summaries = asyncio.run(_measure(args, extra))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(_table(summaries))
    print(f"profile saved: {save_model_profile(backend, summaries[0], summaries[1:])}")
    return 0


SERVER_HINT = (
    "Most of the time is the model thinking. Request-level settings did not help; if your server "
    "is llama.cpp, cap the thinking with --reasoning-budget N (see docs/local-serving.md)."
)


def cmd_tune(args: argparse.Namespace) -> int:
    if args.revert:
        print(revert_tuned())
        return 0
    try:
        config = load_config()
        candidates = candidate_variants()
        backend, summaries = asyncio.run(_measure(args, candidates))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    baseline, rest = summaries[0], summaries[1:]
    print(_table(summaries))
    print(f"profile saved: {save_model_profile(backend, baseline, rest)}")
    best = choose_best(baseline, rest)
    if best is None:
        print("\nrecommendation: keep your current settings (no candidate was clearly better).")
        if baseline.mean_tokens_out > 800:
            print(SERVER_HINT)
        return 0
    variant = next(v for v in candidates if v.name == best.variant)
    print(
        f"\nrecommendation: {variant.name} (same accuracy, "
        f"{best.mean_elapsed_s}s vs {baseline.mean_elapsed_s}s)"
    )
    if args.apply or config.auto_tune == "apply":
        print(
            f"applied: wrote {write_tuned(backend.name, variant)} (undo with: deputy tune --revert)"
        )
    elif config.auto_tune == "off":
        print("auto_tune is off: nothing changed.")
    else:
        print(f"to apply it: deputy tune --apply   (writes {tuned_path()})")
    return 0


def cmd_workspace(args: argparse.Namespace) -> int:
    try:
        if args.action == "create":
            print(workspace.create(args.name, Path(args.repo), args.paths))
        elif args.action == "diff":
            print(workspace.diff(args.name) or "no changes")
        elif args.action == "path":
            print(workspace.work_dir(args.name))
        else:
            workspace.remove(args.name)
            print(f"removed {args.name}")
    except workspace.WorkspaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_mcp(_: argparse.Namespace) -> int:
    from deputy.server import build_server  # noqa: PLC0415  (slow import, only for this command)

    build_server().run("stdio")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deputy", description=__doc__)
    parser.add_argument("--version", action="version", version=f"deputy {_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="write a starter configuration")
    init.add_argument("--name", default="local")
    init.add_argument("--url")
    init.add_argument("--model")
    init.add_argument("--key-file")
    init.add_argument("--force", action="store_true")
    init.add_argument("--yes", action="store_true", help="never ask; use the flags and defaults")
    init.set_defaults(func=cmd_init)

    sub.add_parser("doctor", help="check configuration and backends").set_defaults(func=cmd_doctor)

    inst = sub.add_parser("install", help="connect deputy to an agent")
    inst.add_argument("agent", choices=installer.AGENTS)
    inst.add_argument("--apply", action="store_true", help="do it instead of printing")
    inst.set_defaults(func=cmd_install)

    ins = sub.add_parser("instructions", help="print or write the delegation guide for agents")
    ins.add_argument("--write", metavar="FILE", help="insert it in FILE (CLAUDE.md, ...)")
    ins.set_defaults(func=cmd_instructions)

    for name, helptext, func in (
        ("bench", "measure the chosen model on a built-in suite", cmd_bench),
        ("tune", "measure, then recommend (or apply) better settings", cmd_tune),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--backend", help="backend name (default: the default backend)")
        p.add_argument("--runs", type=int, default=1, help="repetitions of the suite (default 1)")
        p.add_argument("--tasks", help="comma-separated task names")
        if name == "bench":
            p.add_argument("--variant", action="append", help="extra setting: NAME=JSON")
        else:
            p.add_argument("--apply", action="store_true", help="write the winner to tuned.toml")
            p.add_argument("--revert", action="store_true", help="undo the last applied tuning")
        p.set_defaults(func=func)

    ws = sub.add_parser("workspace", help="disposable copies of a repository for workers")
    ws.add_argument("action", choices=["create", "diff", "path", "remove"])
    ws.add_argument("name")
    ws.add_argument("paths", nargs="*", help="files or folders to copy (create only)")
    ws.add_argument("--repo", default=".", help="repository the paths are relative to")
    ws.set_defaults(func=cmd_workspace)

    sub.add_parser("mcp", help="run the MCP server on stdio").set_defaults(func=cmd_mcp)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
