"""Where the orchestrator's tokens went, from the usage Claude Code records for every request.

Reading volume says what a delegation could take; this says what it is worth. A token read once is
replayed on every later request of the same conversation, so a long conversation pays mostly for
carrying its context, not for what it reads.

Weighted tokens put the four kinds of token on one scale, in the price ratios of the Claude API:
input 1, cache write 1.25, cache read 0.1, output 5. They compare shares; they are not an invoice.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, cast

INPUT, CACHE_WRITE, CACHE_READ, OUTPUT = 1.0, 1.25, 0.1, 5.0
# A context above this is worth a look: it is the usual window of a model without extended context.
LARGE_CONTEXT = 200_000


@dataclass
class Spend:
    """What one group of conversations spent."""

    conversations: int = 0
    requests: int = 0
    weighted: float = 0.0
    replay: float = 0.0  # cache reads: the context, read again on every request
    over_large: float = 0.0  # the part of `replay` above LARGE_CONTEXT tokens of context

    def add(self, other: Spend) -> None:
        self.conversations += other.conversations
        self.requests += other.requests
        self.weighted += other.weighted
        self.replay += other.replay
        self.over_large += other.over_large


@dataclass
class Conversation:
    """One transcript: what it spent, and how large its context was on each request."""

    spend: Spend = field(default_factory=Spend)
    contexts: list[int] = field(default_factory=list[int])
    compacted_at: list[int] = field(default_factory=list[int])


@dataclass
class SpendReport:
    main: Spend = field(default_factory=Spend)
    editing: Spend = field(default_factory=Spend)  # subagents that changed files
    reading: Spend = field(default_factory=Spend)  # subagents that did not
    context_median: int = 0  # main sessions: tokens of context per request
    context_p90: int = 0
    compacted_at: int = 0  # main sessions: median context when a compaction came; 0 if none

    @property
    def total(self) -> float:
        return self.main.weighted + self.editing.weighted + self.reading.weighted


def _count(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key)
    return value if isinstance(value, int) else 0


def measure(lines: Iterable[str]) -> Conversation:
    """The spend of one transcript. A request written twice (streaming) counts once."""
    usages: dict[str, dict[str, Any]] = {}
    conversation = Conversation()
    for line in lines:
        compaction = '"isCompactSummary"' in line
        if '"usage"' not in line and not compaction:
            continue
        try:
            event: object = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        record = cast("dict[str, Any]", event)
        if record.get("isCompactSummary") and usages:
            conversation.compacted_at.append(_context(next(reversed(usages.values()))))
        message: object = record.get("message")
        if record.get("type") != "assistant" or not isinstance(message, dict):
            continue
        usage: object = cast("dict[str, Any]", message).get("usage")
        if isinstance(usage, dict):
            key = str(cast("dict[str, Any]", message).get("id") or len(usages))
            usages[key] = cast("dict[str, Any]", usage)
    for usage in usages.values():
        _add(conversation, usage)
    conversation.spend.conversations = 1 if usages else 0
    return conversation


def _context(usage: dict[str, Any]) -> int:
    return (
        _count(usage, "input_tokens")
        + _count(usage, "cache_read_input_tokens")
        + _count(usage, "cache_creation_input_tokens")
    )


def _add(conversation: Conversation, usage: dict[str, Any]) -> None:
    read = _count(usage, "cache_read_input_tokens")
    spend = conversation.spend
    spend.requests += 1
    spend.replay += CACHE_READ * read
    spend.over_large += CACHE_READ * max(read - LARGE_CONTEXT, 0)
    spend.weighted += (
        INPUT * _count(usage, "input_tokens")
        + CACHE_WRITE * _count(usage, "cache_creation_input_tokens")
        + CACHE_READ * read
        + OUTPUT * _count(usage, "output_tokens")
    )
    conversation.contexts.append(_context(usage))


class Collector:
    """Adds conversations up into a report."""

    def __init__(self) -> None:
        self.report = SpendReport()
        self._contexts: list[int] = []
        self._compacted_at: list[int] = []

    def add(self, conversation: Conversation, *, subagent: bool, edits: bool) -> None:
        if not subagent:
            self.report.main.add(conversation.spend)
            self._contexts += conversation.contexts
            self._compacted_at += conversation.compacted_at
        elif edits:
            self.report.editing.add(conversation.spend)
        else:
            self.report.reading.add(conversation.spend)

    def finish(self) -> SpendReport:
        if self._contexts:
            self.report.context_median = int(statistics.median(self._contexts))
            ordered = sorted(self._contexts)
            self.report.context_p90 = ordered[-(-9 * len(ordered) // 10) - 1]  # nearest rank
        if self._compacted_at:
            self.report.compacted_at = int(statistics.median(self._compacted_at))
        return self.report


def _millions(value: float) -> str:
    return f"{value / 1e6:.1f}M" if value >= 1e5 else f"{value:,.0f}"


def _share(part: float, whole: float) -> str:
    return f"{part / whole:.0%}" if whole else "-"


def format_lines(report: SpendReport, delegable_weighted: float) -> list[str]:
    """The spend, and next to it what the reads a delegation could have taken were worth."""
    total = report.total
    if not total:
        return []
    lines = [
        "Where the tokens went (weighted: input 1, cache write 1.25, cache read 0.1, output 5):",
        f"{'':<26}{'requests':>9}{'weighted':>10}{'share':>7}{'of it, context read again':>28}",
    ]
    rows = (
        ("main sessions", report.main),
        (f"subagents that edit ({report.editing.conversations})", report.editing),
        (f"subagents that read ({report.reading.conversations})", report.reading),
    )
    lines += [
        f"{name:<26}{spend.requests:>9}{_millions(spend.weighted):>10}"
        f"{_share(spend.weighted, total):>7}{_share(spend.replay, spend.weighted):>28}"
        for name, spend in rows
        if spend.requests
    ]
    if report.main.requests:
        compacted = (
            f"compacted at about {report.compacted_at:,}"
            if report.compacted_at
            else "never compacted"
        )
        lines.append(
            f"Main sessions: {report.context_median:,} tokens of context per request (median), "
            f"{report.context_p90:,} or less in 9 of 10; {compacted}."
        )
    over = report.main.over_large + report.editing.over_large + report.reading.over_large
    lines += [
        f"Reading again the context above {LARGE_CONTEXT:,} tokens: {_share(over, total)} of "
        "everything. A smaller context cuts it (in Claude Code, `autoCompactWindow`).",
        "The large reads a delegation could have taken: at most "
        f"{_share(delegable_weighted, total)} of everything (each counted with its replays).",
    ]
    return lines
