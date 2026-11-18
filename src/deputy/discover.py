"""`deputy discover`: what the orchestrator read by itself, and how much of it a delegation could
have taken. Reads Claude Code session transcripts on this machine; nothing is sent anywhere.

Volumes are estimates (bytes / 4) of the text in tool results; images are ignored. Volume is not
cost: prompt caching makes a re-read cheaper, and a result is replayed on every later turn. The
numbers are an upper bound on what delegation could save, and a measure of where reading goes.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from deputy import tokens

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


@dataclass
class Bucket:
    results: int = 0
    tokens: int = 0
    large_results: int = 0
    large_tokens: int = 0


@dataclass
class Report:
    days: float
    sessions: int
    total: Bucket = field(default_factory=Bucket)
    by_category: dict[str, Bucket] = field(default_factory=dict[str, Bucket])
    by_source: dict[str, Bucket] = field(default_factory=dict[str, Bucket])
    delegations: int = 0  # calls of deputy's `delegate` tool, to compare with the large reads


def is_delegation(source: str) -> bool:
    """A call of deputy's `delegate` tool, whatever name the agent gave the server."""
    return "deputy" in source and source.endswith("delegate")


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
    """(source, characters) for every tool result of one transcript."""
    calls: dict[str, str] = {}
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
            elif block.get("type") == "tool_result":
                source = calls.get(str(block.get("tool_use_id")), "?")
                yield source, _text_size(block.get("content"))


def _add(bucket: Bucket, chars: int) -> None:
    size = tokens.from_chars(chars)
    bucket.results += 1
    bucket.tokens += size
    if chars > LARGE_CHARS:
        bucket.large_results += 1
        bucket.large_tokens += size


def build_report(folder: Path, *, days: float, now: float | None = None) -> Report:
    since = (now if now is not None else time.time()) - days * 86_400
    files = [f for f in sorted(folder.glob("*.jsonl")) if f.stat().st_mtime >= since]
    report = Report(days=days, sessions=len(files))
    report.by_category = {name: Bucket() for name in CATEGORIES}
    for path in files:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for source, chars in _results(handle):
                _add(report.total, chars)
                _add(report.by_category[category_of(source)], chars)
                _add(report.by_source.setdefault(source, Bucket()), chars)
    report.delegations = sum(
        b.results for name, b in report.by_source.items() if is_delegation(name)
    )
    return report


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "-"


def format_report(report: Report, *, top: int = 8) -> str:
    total = report.total
    if total.results == 0:
        return f"no tool results in the last {report.days:g} days"
    reads = report.by_category["reads"]
    output = report.by_category["command output"]
    lines = [
        f"{report.sessions} sessions, last {report.days:g} days: {total.results} tool results, "
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
        f"Delegations to deputy: {report.delegations}; large reads the orchestrator did itself: "
        f"{reads.large_results}. Compare them over time, before and after the read hint.",
    ]
    return "\n".join(lines)


def report_as_json(report: Report) -> str:
    return json.dumps(asdict(report))
