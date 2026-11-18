"""Offline optimiser for a worker profile: reflective prompt search, judged on the benchmark.

The worker model reads where the current profile failed and proposes rewritten instructions.
Each proposal is measured on the selection tasks, and one is kept only if it loses no accuracy and
is clearly cheaper or more accurate, and then holds up on the held-out tasks the search never saw.
Nothing is applied here: a confirmed proposal is saved with a report, and `deputy optimise
--apply` is the human's approval. `--revert` puts the previous profile back.
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from deputy import bench
from deputy.agent import Result
from deputy.backends import ChatClient
from deputy.bench import Summary, Variant
from deputy.config import Backend, Policy, config_dir, state_dir
from deputy.profiles import Profile

MAX_BODY_CHARS = 4_000
MIN_BODY_CHARS = 200
MAX_FAILURES_SHOWN = 4
ANSWER_EXCERPT_CHARS = 300

STRATEGIES = {
    "fix": "Make the smallest edit that would have prevented the failures below. Keep every "
    "instruction that already works.",
    "lean": "Rewrite the instructions shorter and clearer, keeping every rule that protects "
    "accuracy, safety or the language. Fewer words, same behaviour.",
}

ClientFactory = Callable[[Backend], ChatClient]


@dataclass(frozen=True)
class Failure:
    """One benchmark task the current profile did not pass, and why."""

    task: str
    prompt: str
    problems: str
    answer: str


@dataclass(frozen=True)
class Proposal:
    name: str
    body: str


@dataclass
class Report:
    id: str
    profile: str
    baseline: Summary
    candidates: list[Summary] = field(default_factory=list[Summary])
    winner: str | None = None
    held_out: list[Summary] = field(default_factory=list[Summary])
    confirmed: bool = False
    diff: str = ""


def render_profile(profile: Profile, body: str) -> str:
    """The profile as a file: its front matter (kept) and a new body."""
    lines = [f"name: {profile.name}", f"description: {profile.description}"]
    if profile.max_turns is not None:
        lines.append(f"max_turns: {profile.max_turns}")
    if profile.max_retries is not None:
        lines.append(f"max_retries: {profile.max_retries}")
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body.strip() + "\n"


def failure_from(task_name: str, result: Result) -> Failure:
    prompt = next((t.task for t in bench.tasks() if t.name == task_name), task_name)
    problems = "; ".join(f"{c.name}: {c.detail or 'failed'}" for c in result.checks if not c.passed)
    return Failure(
        task=task_name,
        prompt=prompt,
        problems=problems or f"status {result.status}",
        answer=result.answer[:ANSWER_EXCERPT_CHARS],
    )


def reflection_prompt(profile: Profile, strategy: str, failures: list[Failure]) -> str:
    shown = failures[:MAX_FAILURES_SHOWN]
    seen = "\n".join(
        f"- task: {f.prompt}\n  what failed: {f.problems}\n  the answer began: {f.answer!r}"
        for f in shown
    )
    return (
        "You improve the instructions (a system prompt) of a small worker model that reads files "
        "with tools and answers in a checked format.\n\n"
        f"CURRENT INSTRUCTIONS:\n{profile.body}\n\n"
        f"WHERE THEY FAILED:\n{seen or '- nothing failed; only make them shorter'}\n\n"
        f"{STRATEGIES[strategy]}\n"
        f"Reply with the complete new instructions only: no preface, no code fence, under "
        f"{MAX_BODY_CHARS} characters. Never remove a rule about secrets, the sandbox or the "
        "answer language."
    )


_FENCE = re.compile(r"^```[a-z]*\n|\n```\s*$")


def clean_body(reply: str) -> str | None:
    """The instructions from a reply, or None if it is not usable."""
    body = _FENCE.sub("", reply.strip()).strip()
    if not MIN_BODY_CHARS <= len(body) <= MAX_BODY_CHARS:
        return None
    return body


async def propose(
    backend: Backend, profile: Profile, failures: list[Failure], factory: ClientFactory
) -> list[Proposal]:
    """One proposal per strategy ('fix' only when something failed), asked of the worker itself."""
    proposals: list[Proposal] = []
    for strategy in STRATEGIES:
        if strategy == "fix" and not failures:
            continue
        client = factory(backend)
        try:
            reply = await client.chat(
                [{"role": "user", "content": reflection_prompt(profile, strategy, failures)}]
            )
        finally:
            await client.aclose()
        body = clean_body(reply.content)
        if body and body != profile.body:
            proposals.append(Proposal(strategy, body))
    return proposals


def better(baseline: Summary, candidate: Summary) -> bool:
    """Accuracy up without losing citations, or no accuracy lost and clearly cheaper."""
    if candidate.citation_rate < baseline.citation_rate:
        return False
    if candidate.pass_rate > baseline.pass_rate:
        return True
    return bench.choose_best(baseline, [candidate]) is not None


def pick(baseline: Summary, candidates: list[Summary]) -> Summary | None:
    """The best candidate by accuracy, then by speed; None keeps the profile."""
    winners = [c for c in candidates if better(baseline, c)]
    return min(winners, key=lambda c: (-c.pass_rate, c.mean_elapsed_s), default=None)


@dataclass(frozen=True)
class Judge:
    """How a profile is measured: always the same way, so candidates are comparable."""

    backend: Backend
    runs: int
    factory: ClientFactory
    policy: Policy | None

    async def measure(
        self,
        profile: Profile,
        name: str,
        only: list[str],
        on_result: Callable[[str, Result], None] | None = None,
    ) -> Summary:
        return await bench.run_suite(
            self.backend,
            Variant(name=name),
            runs=self.runs,
            only=only,
            profile=profile,
            client_factory=self.factory,
            policy=self.policy,
            on_result=on_result,
        )


async def optimise(  # noqa: PLR0913 - one entry point; the rest are keyword-only knobs
    backend: Backend,
    profile: Profile,
    *,
    runs: int = 1,
    factory: ClientFactory = ChatClient,
    policy: Policy | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> tuple[Report, Proposal | None]:
    """Measure, reflect, judge, confirm. Returns the report and the proposal to keep (if any)."""
    judge = Judge(backend, runs, factory, policy)
    selection, holdout = bench.selection_names(), bench.holdout_names()
    failures: list[Failure] = []

    def note(name: str, result: Result) -> None:
        if result.status != "ok":
            failures.append(failure_from(name, result))

    log("measuring the current profile")
    baseline = await judge.measure(profile, "current", selection, note)
    report = Report(id=time.strftime("%Y%m%d-%H%M%S"), profile=profile.name, baseline=baseline)
    proposals = await propose(backend, profile, failures, factory)
    by_name = {p.name: p for p in proposals}
    for proposal in proposals:
        log(f"measuring proposal '{proposal.name}'")
        trial = replace(profile, body=proposal.body)
        report.candidates.append(await judge.measure(trial, proposal.name, selection))
    best = pick(baseline, report.candidates)
    if best is None:
        return report, None
    winner = by_name[best.variant]
    report.winner = winner.name
    report.diff = "\n".join(
        difflib.unified_diff(
            profile.body.splitlines(), winner.body.splitlines(), "current", winner.name, lineterm=""
        )
    )
    log("confirming on the held-out tasks")
    held_base = await judge.measure(profile, "current", holdout)
    held_win = await judge.measure(replace(profile, body=winner.body), winner.name, holdout)
    report.held_out = [held_base, held_win]
    report.confirmed = bench.confirms(held_base, held_win)
    return report, winner if report.confirmed else None


TABLE_HEAD = (
    "| variant | passed | citations | turns | tokens out | seconds |\n"
    "| --- | --- | --- | --- | --- | --- |"
)


def _row(summary: Summary) -> str:
    return (
        f"| {summary.variant} | {summary.pass_rate:.0%} | {summary.citation_rate:.0%} | "
        f"{summary.mean_turns} | {summary.mean_tokens_out} | {summary.mean_elapsed_s} |"
    )


def to_markdown(report: Report) -> str:
    head = TABLE_HEAD
    lines = [
        f"# Optimiser report {report.id} for profile `{report.profile}`",
        "",
        "## Selection tasks",
    ]
    lines += ["", head, _row(report.baseline), *map(_row, report.candidates)]
    if report.held_out:
        lines += ["", "## Held-out tasks (never seen by the search)", "", head]
        lines += [_row(s) for s in report.held_out]
    if report.winner is None:
        lines += ["", "**Decision: keep the current profile.** No proposal was clearly better."]
    elif not report.confirmed:
        lines += [
            "",
            f"**Decision: keep the current profile.** `{report.winner}` won on the selection "
            "tasks but did not hold up on the held-out ones.",
        ]
    else:
        lines += [
            "",
            f"**Decision: proposal `{report.winner}` is confirmed.** Review it, then apply with "
            f"`deputy optimise --apply {report.id}`.",
            "",
            "```diff",
            report.diff,
            "```",
        ]
    return "\n".join(lines) + "\n"


# --- saving, applying, reverting ---------------------------------------------------------------


def home() -> Path:
    return state_dir() / "optimiser"


def save(report: Report, profile: Profile, winner: Proposal | None) -> Path:
    folder = home() / report.id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.md").write_text(to_markdown(report), encoding="utf-8")
    meta = {"profile": profile.name, "confirmed": winner is not None, "state": "proposed"}
    if winner is not None:
        (folder / "candidate.md").write_text(render_profile(profile, winner.body), encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return folder


class OptimiserError(Exception):
    """A proposal that cannot be applied or reverted; the message says why."""


def _load(proposal_id: str) -> tuple[Path, dict[str, object]]:
    folder = home() / proposal_id
    if not re.fullmatch(r"[0-9-]+", proposal_id) or not (folder / "meta.json").is_file():
        raise OptimiserError(f"no proposal '{proposal_id}' (see: deputy optimise --list)")
    return folder, json.loads((folder / "meta.json").read_text(encoding="utf-8"))


def apply(proposal_id: str) -> Path:
    """Install a confirmed proposal as the user's profile, keeping the previous file."""
    folder, meta = _load(proposal_id)
    if not (folder / "candidate.md").is_file():
        raise OptimiserError(
            "this proposal was not confirmed on the held-out tasks: nothing to apply"
        )
    if meta["state"] == "applied":
        raise OptimiserError("this proposal is already applied")
    target = config_dir() / "profiles" / f"{meta['profile']}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        shutil.copyfile(target, folder / "previous.md")
    shutil.copyfile(folder / "candidate.md", target)
    meta["state"] = "applied"
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return target


def revert(proposal_id: str) -> Path:
    """Undo `apply`: the previous user profile comes back, or the file goes if there was none."""
    folder, meta = _load(proposal_id)
    if meta["state"] != "applied":
        raise OptimiserError("this proposal is not applied")
    target = config_dir() / "profiles" / f"{meta['profile']}.md"
    if (folder / "previous.md").is_file():
        shutil.copyfile(folder / "previous.md", target)
    else:
        target.unlink(missing_ok=True)
    meta["state"] = "reverted"
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return target


def listing() -> list[str]:
    """One line per saved proposal, newest first."""
    lines: list[str] = []
    for folder in sorted(home().glob("*/meta.json"), reverse=True):
        meta = json.loads(folder.read_text(encoding="utf-8"))
        kind = "confirmed" if meta["confirmed"] else "not confirmed"
        lines.append(f"{folder.parent.name}  {meta['profile']}  {kind}  {meta['state']}")
    return lines
