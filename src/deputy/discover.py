"""`deputy discover`: what the orchestrator read by itself, and how much of it a delegation could
have taken. Reads Claude Code session transcripts on this machine; nothing is sent anywhere.

Volumes are estimates (bytes / 4) of the text in tool results; images are ignored. Volume is not
cost: prompt caching makes a re-read cheaper, and a result is replayed on every later turn. The
numbers are an upper bound on what delegation could save, and a measure of where reading goes.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from deputy import shellreads, tokens

# A result larger than this is one a delegation can pay for (see `small_input`: about 1,000 tokens).
LARGE_CHARS = 4_000
_READ_TOOLS = frozenset({"Read", "Grep", "Glob", "NotebookRead", "WebFetch"})
_READ_COMMANDS = frozenset(
    {"cat", "sed", "head", "tail", "grep", "rg", "find", "ls", "wc", "tree", "awk", "jq", "less"}
)
# Output that a command compressor (such as rtk) shortens; a delegation does not help here.
_COMMAND_OUTPUT = frozenset(
    {"git", "gh", "npm", "pnpm", "yarn", "npx", "pytest", "cargo", "go", "docker", "kubectl",
     "make", "uv", "node", "ruff", "eslint", "tsc", "mvn", "gradle"}
)  # fmt: skip
_PREFIX = re.compile(r"^\s*(?:cd\s+[^;&|]+(?:&&|;)\s*|[A-Z_]+=\S*\s+)*")
CATEGORIES = ("reads", "command output", "other")
_EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
# A file edited this soon after a read was read to be edited: the exact text was needed.
EDIT_WINDOW = 40


@dataclass
class Bucket:
    results: int = 0
    tokens: int = 0
    large_results: int = 0
    large_tokens: int = 0


@dataclass
class Adoption:
    """Large file reads, and how many of them a delegation could have taken."""

    file_reads: int = 0
    file_tokens: int = 0
    delegable_reads: int = 0  # not followed by an edit of the same file
    delegable_tokens: int = 0
    again_tokens: int = 0  # a file already read, unchanged since
    same_part_tokens: int = 0  # of those, the very same part of the file


@dataclass
class Report:
    days: float
    sessions: int
    total: Bucket = field(default_factory=Bucket)
    by_category: dict[str, Bucket] = field(default_factory=dict[str, Bucket])
    by_source: dict[str, Bucket] = field(default_factory=dict[str, Bucket])
    delegations: int = 0  # calls of deputy's delegation tools, to compare with the large reads
    adoption: Adoption = field(default_factory=Adoption)
    subagents: int = 0  # subagent transcripts read
    agent_runs: int = 0  # `delegate` calls with agent=true
    agent_changes: int = 0  # the code changes in them: one per step
    own_edits: int = 0  # edits the sessions made themselves
    subagent_adoption: Adoption = field(default_factory=Adoption)  # the part of `adoption` in them

    @property
    def adoption_rate(self) -> float:
        """Delegations among the reads a delegation could have taken."""
        whole = self.delegations + self.adoption.delegable_reads
        return self.delegations / whole if whole else 0.0


def is_delegation(source: str) -> bool:
    """A call of deputy's `delegate` or `digest`, whatever name the agent gave the server."""
    return "deputy" in source and source.endswith(("delegate", "digest"))


def transcripts_dir(project: Path) -> Path:
    """Where Claude Code keeps the sessions of a project folder."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(project.resolve()))
    return Path.home() / ".claude" / "projects" / slug


def _text_size(content: object) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        blocks = cast("list[object]", content)
        return sum(len(str(_as_dict(b).get("text", ""))) for b in blocks if _is_text(b))
    return 0


def _as_dict(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _is_text(block: object) -> bool:
    return _as_dict(block).get("type") == "text"


def source_of(name: str, arguments: dict[str, Any]) -> str:
    """The tool, or for a shell call its first command word (`Bash: git`)."""
    if name != "Bash":
        return name
    command = _PREFIX.sub("", str(arguments.get("command", "")))
    word = command.split(maxsplit=1)[0] if command.split() else "?"
    return f"Bash: {Path(word).name}"


def category_of(source: str) -> str:
    if source in _READ_TOOLS:
        return "reads"
    if source.startswith("Bash: "):
        word = source.removeprefix("Bash: ")
        if word in _READ_COMMANDS:
            return "reads"
        if word in _COMMAND_OUTPUT:
            return "command output"
    return "other"


def _results(lines: Iterable[str]) -> Iterator[tuple[str, int]]:
    """(source, characters) for every tool result of one transcript, each counted once."""
    calls: dict[str, str] = {}
    counted: set[str] = set()
    for line in lines:
        try:
            event: object = json.loads(line)
        except ValueError:
            continue
        content: object = _as_dict(_as_dict(event).get("message")).get("content")
        if not isinstance(content, list):
            continue
        for raw in cast("list[object]", content):
            block = _as_dict(raw)
            if block.get("type") == "tool_use":
                calls[str(block.get("id"))] = source_of(
                    str(block.get("name")), _as_dict(block.get("input"))
                )
            elif block.get("type") == "tool_result" and block.get("tool_use_id") not in counted:
                counted.add(str(block.get("tool_use_id")))
                source = calls.get(str(block.get("tool_use_id")), "?")
                yield source, _text_size(block.get("content"))


@dataclass
class _Step:
    """One tool call of a session, in order; or a compaction, which forgets what was read."""

    reads: list[tuple[str, tuple[object, ...]]] = field(
        default_factory=list[tuple[str, tuple[object, ...]]]
    )  # (file, which part of it)
    edits: list[str] = field(default_factory=list[str])
    chars: int = 0
    compaction: bool = False
    agent_changes: int = 0  # code changes handed to the local agent by this call


def _resolved(path: str, folder: str) -> str:
    return os.path.normpath(os.path.join(folder, path))


def _step_of(name: str, arguments: dict[str, Any], cwd: str) -> _Step:
    target = str(arguments.get("file_path") or arguments.get("notebook_path") or "")
    if name in _EDIT_TOOLS and target:
        return _Step(edits=[_resolved(target, cwd)])
    if name == "Read" and target:
        part = (arguments.get("offset"), arguments.get("limit"))
        return _Step(reads=[(_resolved(target, cwd), part)])
    if is_delegation(name) and arguments.get("agent") is True:
        steps = arguments.get("steps")
        return _Step(agent_changes=len(cast("list[object]", steps)) if steps else 1)
    if name != "Bash":
        return _Step()
    command = str(arguments.get("command", ""))
    folder = os.path.join(cwd, shellreads.working_folder(command) or "")
    return _Step(
        reads=[
            (_resolved(r.path, folder), (r.first, r.last, r.from_end, r.pattern))
            for r in shellreads.shell_reads(command)
        ],
        edits=[_resolved(path, folder) for path in shellreads.shell_edits(command)],
    )


def _steps(lines: Iterable[str]) -> list[_Step]:
    """The tool calls of one transcript, each with the size of its result."""
    steps: list[_Step] = []
    by_id: dict[str, _Step] = {}
    for line in lines:
        try:
            event = _as_dict(json.loads(line))
        except ValueError:
            continue
        if event.get("isCompactSummary"):
            steps.append(_Step(compaction=True))
        content: object = _as_dict(event.get("message")).get("content")
        for raw in cast("list[object]", content) if isinstance(content, list) else []:
            block = _as_dict(raw)
            if block.get("type") == "tool_use" and str(block.get("id")) not in by_id:
                arguments = _as_dict(block.get("input"))
                step = _step_of(str(block.get("name")), arguments, str(event.get("cwd") or "/"))
                by_id[str(block.get("id"))] = step
                steps.append(step)
            elif block.get("type") == "tool_result" and str(block.get("tool_use_id")) in by_id:
                by_id[str(block.get("tool_use_id"))].chars = _text_size(block.get("content"))
    return steps


def _count_large_read(into: Adoption, steps: list[_Step], index: int, seen: set[Any]) -> None:
    step = steps[index]
    size = tokens.from_chars(step.chars)
    files = {path for path, _ in step.reads}
    into.file_reads += 1
    into.file_tokens += size
    later = steps[index + 1 : index + 1 + EDIT_WINDOW]
    if not any(files.intersection(s.edits) for s in later):
        into.delegable_reads += 1
        into.delegable_tokens += size
    if files & seen:
        into.again_tokens += size
        if all(read in seen for read in step.reads):
            into.same_part_tokens += size


def _transcripts(folder: Path, since: float) -> tuple[list[Path], set[Path]]:
    """The session transcripts of a project folder and, apart, those of their subagents."""
    subagents = {f for f in folder.glob("*/subagents/*.jsonl") if f.stat().st_mtime >= since}
    sessions = [f for f in sorted(folder.glob("*.jsonl")) if f.stat().st_mtime >= since]
    return [*sessions, *sorted(subagents)], subagents


def _merge(into: Adoption, part: Adoption) -> None:
    for name, value in asdict(part).items():
        setattr(into, name, getattr(into, name) + value)


def _measure_adoption(into: Adoption, steps: list[_Step]) -> None:
    seen: set[Any] = set()  # files, and (file, part) pairs, read and unchanged since
    for index, step in enumerate(steps):
        if step.compaction:
            seen.clear()
        for path in step.edits:
            seen = {s for s in seen if s != path and not (isinstance(s, tuple) and s[0] == path)}
        if step.reads and step.chars > LARGE_CHARS:
            _count_large_read(into, steps, index, seen)
        for read in step.reads:
            seen |= {read[0], read}


def _add(bucket: Bucket, chars: int) -> None:
    size = tokens.from_chars(chars)
    bucket.results += 1
    bucket.tokens += size
    if chars > LARGE_CHARS:
        bucket.large_results += 1
        bucket.large_tokens += size


def build_report(folder: Path, *, days: float, now: float | None = None) -> Report:
    since = (now if now is not None else time.time()) - days * 86_400
    files, subagents = _transcripts(folder, since)
    report = Report(days=days, sessions=len(files) - len(subagents), subagents=len(subagents))
    report.by_category = {name: Bucket() for name in CATEGORIES}
    for path in files:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for source, chars in _results(handle):
                _add(report.total, chars)
                _add(report.by_category[category_of(source)], chars)
                _add(report.by_source.setdefault(source, Bucket()), chars)
            handle.seek(0)
            part = Adoption()
            steps = _steps(handle)
            report.agent_runs += sum(1 for step in steps if step.agent_changes)
            report.agent_changes += sum(step.agent_changes for step in steps)
            report.own_edits += sum(len(step.edits) for step in steps)
            _measure_adoption(part, steps)
            _merge(report.adoption, part)
            if path in subagents:
                _merge(report.subagent_adoption, part)
    report.delegations = sum(
        b.results for name, b in report.by_source.items() if is_delegation(name)
    )
    return report


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "-"


def _adoption_lines(report: Report) -> list[str]:
    seen = report.adoption
    agents = report.subagent_adoption
    return [
        f"Adoption: {report.adoption_rate:.1%} ({report.delegations} delegations; "
        f"{seen.delegable_reads} large file reads a delegation could have taken, "
        f"~{seen.delegable_tokens:,} tokens).",
        f"Large file reads: {seen.file_reads}, ~{seen.file_tokens:,} tokens; the others were "
        f"followed by an edit of the file within {EDIT_WINDOW} tool calls.",
        f"Of the reads a delegation could have taken, subagents did {agents.delegable_reads} "
        f"(~{agents.delegable_tokens:,} tokens) and the main sessions the rest.",
        f"Read again with no change or compaction in between: ~{seen.again_tokens:,} tokens, "
        f"~{seen.same_part_tokens:,} of them the very same part of the file.",
        f"Code changes: {report.agent_changes} handed to the local agent in {report.agent_runs} "
        f"run(s); the sessions made {report.own_edits} edits themselves.",
    ]


def format_report(report: Report, *, top: int = 8) -> str:
    total = report.total
    if total.results == 0:
        return f"no tool results in the last {report.days:g} days"
    reads = report.by_category["reads"]
    output = report.by_category["command output"]
    lines = [
        f"{report.sessions} sessions and {report.subagents} subagents, last {report.days:g} days: "
        f"{total.results} tool results, "
        f"~{total.tokens:,} tokens read by the orchestrator (bytes / 4, text only)",
        "",
        f"{'':<22}{'results':>9}{'tokens':>12}{'share':>7}{'large*':>9}{'in large':>11}",
    ]
    for name in CATEGORIES:
        b = report.by_category[name]
        lines.append(
            f"{name:<22}{b.results:>9}{b.tokens:>12,}{_share(b.tokens, total.tokens):>7}"
            f"{b.large_results:>9}{b.large_tokens:>11,}"
        )
    lines += ["", "largest sources of large results:"]
    ranked = sorted(report.by_source.items(), key=lambda kv: -kv[1].large_tokens)[:top]
    lines += [
        f"  {name:<20}{b.large_results:>6} large, ~{b.large_tokens:,} tokens"
        for name, b in ranked
        if b.large_tokens
    ]
    lines += [
        "",
        f"* large = over {LARGE_CHARS:,} characters (about 1,000 tokens): a delegation can pay.",
        f"A delegation could take up to ~{reads.large_tokens:,} tokens of large reads "
        f"({_share(reads.large_tokens, total.tokens)} of all); a command-output compressor such as "
        f"rtk works on the ~{output.tokens:,} tokens of command output "
        f"({_share(output.tokens, total.tokens)}).",
        "Upper bounds on volume, not cost: prompt caching makes re-reads cheaper.",
        "",
        *_adoption_lines(report),
    ]
    return "\n".join(lines)


def report_as_json(report: Report) -> str:
    return json.dumps({**asdict(report), "adoption_rate": round(report.adoption_rate, 4)})
