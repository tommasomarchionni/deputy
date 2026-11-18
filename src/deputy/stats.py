"""A usage report built from the trace: runs, outcomes, acceptance and estimated tokens saved."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from deputy.config import state_dir
from deputy.runs import BENCH_SOURCE

SECONDS_PER_DAY = 86_400


@dataclass
class Group:
    """Counters for one slice of the runs (everything, one profile, or one task class)."""

    key: str
    runs: int = 0
    ok: int = 0
    escalated: int = 0  # handed back to the orchestrator (`needs_frontier`)
    retried: int = 0
    accepted: int = 0
    rejected: int = 0
    tokens_saved_est: int = 0
    read_est: int = 0
    cost_est: int = 0
    estimated: int = 0  # runs that record how their saving was estimated (read and cost)
    seconds: float = 0.0

    @property
    def pass_rate(self) -> float:
        return self.ok / self.runs if self.runs else 0.0

    @property
    def acceptance_rate(self) -> float | None:
        judged = self.accepted + self.rejected
        return self.accepted / judged if judged else None

    @property
    def mean_saved(self) -> float:
        return self.tokens_saved_est / self.estimated if self.estimated else 0.0

    @property
    def mean_read(self) -> float:
        return self.read_est / self.estimated if self.estimated else 0.0

    @property
    def mean_cost(self) -> float:
        return self.cost_est / self.estimated if self.estimated else 0.0

    @property
    def mean_seconds(self) -> float:
        return self.seconds / self.runs if self.runs else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "pass_rate": round(self.pass_rate, 3),
            "acceptance_rate": None
            if self.acceptance_rate is None
            else round(self.acceptance_rate, 3),
            "mean_saved": round(self.mean_saved, 1),
            "mean_read": round(self.mean_read, 1),
            "mean_cost": round(self.mean_cost, 1),
            "mean_seconds": round(self.mean_seconds, 1),
        }


@dataclass
class Report:
    days: float
    total: Group
    by_profile: list[Group]
    by_class: list[Group]
    unreadable_lines: int


def read_trace() -> tuple[list[dict[str, Any]], int]:
    """Every event of the trace, and how many lines could not be read (never fatal)."""
    path = state_dir() / "runs.jsonl"
    if not path.is_file():
        return [], 0
    events: list[dict[str, Any]] = []
    unreadable = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            unreadable += 1
            continue
        if isinstance(event, dict):
            events.append(event)  # pyright: ignore[reportUnknownArgumentType]
        else:
            unreadable += 1
    return events, unreadable


def _verdicts(events: Iterable[dict[str, Any]]) -> dict[str, str]:
    """The last verdict given for each run."""
    return {
        str(e["run_id"]): str(e["verdict"])
        for e in events
        if e.get("event") == "feedback" and "run_id" in e and "verdict" in e
    }


def _add(group: Group, event: dict[str, Any], verdict: str | None) -> None:
    metrics: dict[str, Any] = event.get("metrics") or {}
    group.runs += 1
    group.ok += event.get("status") == "ok"
    group.escalated += "needs_frontier" in (event.get("flags") or [])
    group.retried += bool(metrics.get("retries"))
    group.accepted += verdict == "accepted"
    group.rejected += verdict == "rejected"
    if "read_est" in event and "cost_est" in event:
        # Older versions recorded only a total, from an estimate that could count a whole file
        # per search hit (millions of tokens in one run): not comparable, so not summed.
        group.estimated += 1
        group.tokens_saved_est += int(event.get("saved_est") or 0)
        group.read_est += int(event.get("read_est") or 0)
        group.cost_est += int(event.get("cost_est") or 0)
    group.seconds += float(metrics.get("elapsed_s") or 0.0)


def build_report(
    events: list[dict[str, Any]],
    *,
    days: float,
    unreadable: int = 0,
    now: float | None = None,
    include_bench: bool = False,
) -> Report:
    """Aggregate the runs of the last `days` days, joining each to the feedback given for it.

    Benchmark runs are synthetic traffic, so they are left out unless `include_bench` is set; a
    line without a source (an older trace) counts as real use."""
    since = (now if now is not None else time.time()) - days * SECONDS_PER_DAY
    verdicts = _verdicts(events)
    total = Group("all")
    profiles: dict[str, Group] = {}
    classes: dict[str, Group] = {}
    for event in events:
        if event.get("event") != "run" or float(event.get("ts") or 0) < since:
            continue
        if event.get("source") == BENCH_SOURCE and not include_bench:
            continue
        profile = str(event.get("profile") or "unknown")
        task_class = str(event.get("task_class") or f"{profile}:unknown")
        verdict = verdicts.get(str(event.get("run_id")))
        for group in (
            total,
            profiles.setdefault(profile, Group(profile)),
            classes.setdefault(task_class, Group(task_class)),
        ):
            _add(group, event, verdict)
    return Report(
        days=days,
        total=total,
        by_profile=sorted(profiles.values(), key=lambda g: -g.runs),
        by_class=sorted(classes.values(), key=lambda g: -g.runs),
        unreadable_lines=unreadable,
    )


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def _row(group: Group, width: int) -> str:
    return (
        f"{group.key:<{width}}{group.runs:>6}{_percent(group.pass_rate):>6}{group.escalated:>6}"
        f"{group.retried:>6}{_percent(group.acceptance_rate):>9}{group.tokens_saved_est:>9}"
        f"{group.mean_read:>7.0f}{group.mean_cost:>7.0f}{group.mean_seconds:>7.1f}"
    )


def format_report(report: Report) -> str:
    if report.total.runs == 0:
        return f"no runs in the last {report.days:g} days"
    width = max(14, *(len(g.key) for g in [*report.by_profile, *report.by_class])) + 2
    head = (
        f"{'':<{width}}{'runs':>6}{'ok':>6}{'esc':>6}{'retry':>6}{'accepted':>9}"
        f"{'saved*':>9}{'read':>7}{'cost':>7}{'sec':>7}"
    )
    lines = [f"Last {report.days:g} days", head, _row(report.total, width)]
    if len(report.by_profile) > 1:
        lines += ["", "by profile", *(_row(g, width) for g in report.by_profile)]
    lines += ["", "by task class", *(_row(g, width) for g in report.by_class)]
    lines += [
        "",
        "* estimated tokens spared to the orchestrator: what the worker read (read, per run) minus",
        "  what the orchestrator paid (cost, per run: its request and the result); can be below 0",
        "esc = handed back to the orchestrator; accepted = share of judged runs (feedback)",
    ]
    older = report.total.runs - report.total.estimated
    if older:
        lines.append(f"({older} run(s) from older versions are counted, but not their saving)")
    if report.unreadable_lines:
        lines.append(f"({report.unreadable_lines} unreadable trace line(s) were skipped)")
    return "\n".join(lines)


MIN_RUNS_FOR_ADVICE = 5
MAX_ESCALATION_RATE = 0.5
ADVICE_FLAG = "not_worth_delegating"
_TRACE_TAIL_LINES = 5_000


@dataclass(frozen=True)
class Advice:
    """Why a kind of task is better done by the orchestrator itself."""

    task_class: str
    runs: int
    mean_saved: float
    escalation_rate: float
    reason: str


def advice_for(group: Group) -> Advice | None:
    """A task class that has been tried enough and does not pay: no net token saving, or most
    runs handed back. Too few runs say nothing, so there is no advice yet."""
    if group.runs < MIN_RUNS_FOR_ADVICE:
        return None
    rate = group.escalated / group.runs
    if group.estimated >= MIN_RUNS_FOR_ADVICE and group.mean_saved <= 0:
        reason = f"no net token saving (mean {group.mean_saved:.0f} per run)"
    elif rate > MAX_ESCALATION_RATE:
        reason = f"{rate:.0%} of runs were handed back to the orchestrator"
    else:
        return None
    return Advice(group.key, group.runs, group.mean_saved, rate, reason)


def delegation_advice(report: Report) -> list[Advice]:
    """Every task class of the report that is not worth delegating."""
    return [a for g in report.by_class if (a := advice_for(g))]


def _recent_events() -> tuple[list[dict[str, Any]], int]:
    """The trace, but only its tail: advice runs on every delegation and must stay cheap."""
    events, unreadable = read_trace()
    return events[-_TRACE_TAIL_LINES:], unreadable


def advice_for_class(task_class: str, *, days: float = 30) -> Advice | None:
    events, unreadable = _recent_events()
    report = build_report(events, days=days, unreadable=unreadable)
    return next((a for a in delegation_advice(report) if a.task_class == task_class), None)


def advice_lines(*, days: float = 30, limit: int = 3) -> list[str]:
    """One sentence per task class that is not worth delegating, for the `status` tool."""
    events, unreadable = _recent_events()
    report = build_report(events, days=days, unreadable=unreadable)
    return [
        f"{a.task_class}: {a.runs} runs, {a.reason}; do these yourself"
        for a in delegation_advice(report)[:limit]
    ]


def usage_line(days: float = 7) -> str | None:
    """A one-line summary for the `status` tool, or None when there is nothing to say."""
    events, unreadable = read_trace()
    total = build_report(events, days=days, unreadable=unreadable).total
    if total.runs == 0:
        return None
    judged = _percent(total.acceptance_rate)
    return (
        f"{days:g}d: {total.runs} runs, {total.pass_rate:.0%} ok, {total.escalated} handed back, "
        f"~{total.tokens_saved_est} tokens saved, {judged} accepted"
    )


def report_as_json(report: Report) -> str:
    return json.dumps(
        {
            "days": report.days,
            "total": report.total.as_dict(),
            "by_profile": [g.as_dict() for g in report.by_profile],
            "by_class": [g.as_dict() for g in report.by_class],
            "unreadable_lines": report.unreadable_lines,
        }
    )
