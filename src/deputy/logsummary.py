"""A deterministic summary of a repetitive file (a log, a test run): nothing but repetition is lost.

Every line is reduced to a template (its digit runs become `#1`, `#2`, ...). Each distinct template
is reported once with how many lines it stands for, the first and last line number, a real example,
and the values of the numeric fields that take few distinct values (a worker id, a status code).
Lines that occur rarely are shown whole with their own line number, so a unique error is never
folded away. No model is involved, so a large log costs seconds instead of minutes.

The idea, never to print a summary that hides a kind of line, comes from measuring the `log` filter
of the rtk project on a real CI log: it reduced 7,700 tokens to 54 by keeping only lines with an
explicit level, and dropped most of the log.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

RARE_MAX = 3  # a template seen at most this many times is shown line by line
MAX_FIELD_VALUES = 8  # a numeric field is itemised only if it takes this few distinct values
MAX_LINE_CHARS = 200
MAX_CHARS = 12_000
_DIGITS = re.compile(r"\d+")


@dataclass
class _Group:
    template: str
    first: int
    last: int
    example: str
    count: int = 0
    rows: list[tuple[int, str]] = field(default_factory=list[tuple[int, str]])
    fields: list[Counter[str]] = field(default_factory=list[Counter[str]])


def _template(line: str) -> str:
    counter = iter(range(1, 10_000))
    return _DIGITS.sub(lambda _m: f"#{next(counter)}", line)


def _groups(lines: list[str]) -> list[_Group]:
    groups: dict[str, _Group] = {}
    for number, raw in enumerate(lines, start=1):
        line = raw[:MAX_LINE_CHARS]
        key = _template(line)
        group = groups.setdefault(key, _Group(key, number, number, line))
        group.count += 1
        group.last = number
        if group.count <= RARE_MAX:
            group.rows.append((number, line))
        values = _DIGITS.findall(line)
        while len(group.fields) < len(values):
            group.fields.append(Counter())
        for index, value in enumerate(values):
            group.fields[index][value] += 1
    return list(groups.values())


def _itemised(group: _Group) -> str:
    parts: list[str] = []
    for index, values in enumerate(group.fields, start=1):
        if 1 < len(values) <= MAX_FIELD_VALUES:
            shown = ", ".join(f"{value}x{count}" for value, count in sorted(values.items()))
            parts.append(f"#{index}: {shown}")
    return "; ".join(parts)


def summarise(lines: list[str]) -> str:
    """The summary of `lines` (line numbers are those of the file, starting at 1)."""
    if not lines:
        return "[empty file]"
    groups = _groups(lines)
    rare = [g for g in groups if g.count <= RARE_MAX]
    repeated = [g for g in groups if g.count > RARE_MAX]
    out = [f"[{len(lines)} lines, {len(groups)} distinct kinds of line]"]
    if rare:
        out.append("Lines that occur rarely (shown whole):")
        out += [f"{number}: {line}" for g in rare for number, line in g.rows]
    if repeated:
        out.append("Repeated lines (kind, lines first-last, count; varying numbers are #N):")
        for g in sorted(repeated, key=lambda g: g.first):
            detail = _itemised(g)
            suffix = f"\n    values: {detail}" if detail else ""
            out.append(f"{g.first}-{g.last} x{g.count}: {g.template}\n    e.g. {g.example}{suffix}")
    text = "\n".join(out)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n[cut: too many distinct kinds of line; read the file in parts]"
    return text
