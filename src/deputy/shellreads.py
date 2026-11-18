"""Which files a shell command prints: `cat`, `sed -n`, `head`, `tail` and `nl`.

Used to see large reads that do not go through a read tool. Anything unusual (a redirection, an
in-place edit, a follow, a substitution) means "not a plain read": the answer is then no files, so
a wrong guess can only miss a read and never claim one.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, replace

_SEGMENTS = re.compile(r"&&|\|\||;|\n")
_UNSAFE = re.compile(r"[<>`]|\$\(")
_QUIET = re.compile(r"\s2>(?:/dev/null|&1)")  # hiding or joining errors changes no file
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SED_RANGE = re.compile(r"^(\d+)(?:,(\d+))?p$")
_COUNT = re.compile(r"^-(\d+)$")


@dataclass(frozen=True)
class ShellRead:
    """A file a command prints: all of it, or the lines `first` to `last` (a None end is open)."""

    path: str
    first: int | None = None
    last: int | None = None
    from_end: int | None = None  # `tail -n N`: the last N lines
    filtered: bool = False  # piped into another command, so less than this may be printed


def _words(segment: str) -> list[str]:
    try:
        words = shlex.split(segment)
    except ValueError:
        return []
    while words and _ASSIGNMENT.match(words[0]):
        words = words[1:]
    return words


def _cat(args: list[str]) -> list[ShellRead]:
    if any(a.startswith("-") for a in args):
        return []
    return [ShellRead(a) for a in args]


def _sed(args: list[str]) -> list[ShellRead]:
    """Only `sed -n 'A,Bp' FILE...`, the form that prints a range and changes nothing."""
    if len(args) < 3 or args[0] != "-n":
        return []
    found = _SED_RANGE.match(args[1])
    if found is None:
        return []
    first = int(found[1])
    last = int(found[2]) if found[2] else first
    return [ShellRead(a, first=first, last=last) for a in args[2:] if not a.startswith("-")]


def _line_count(args: list[str]) -> tuple[int, list[str]] | None:
    """(lines asked for, the remaining words) of `head`/`tail`; None for anything else."""
    count = 10
    rest = list(args)
    if rest and rest[0] == "-n" and len(rest) > 1 and rest[1].isdigit():
        count = int(rest[1])
        rest = rest[2:]
    elif rest and (found := _COUNT.match(rest[0])):
        count = int(found[1])
        rest = rest[1:]
    if any(a.startswith(("-", "+")) for a in rest):
        return None
    return count, rest


def _head(args: list[str]) -> list[ShellRead]:
    parsed = _line_count(args)
    if parsed is None:
        return []
    count, files = parsed
    return [ShellRead(a, first=1, last=count) for a in files]


def _tail(args: list[str]) -> list[ShellRead]:
    parsed = _line_count(args)
    if parsed is None:
        return []
    count, files = parsed
    return [ShellRead(a, from_end=count) for a in files]


_READERS = {"cat": _cat, "nl": _cat, "sed": _sed, "head": _head, "tail": _tail}


def _printed(segment: str) -> list[ShellRead]:
    """The first stage of a pipeline decides what is read; later stages only filter it."""
    stage, pipe, _ = segment.partition("|")
    words = _words(stage)
    reader = _READERS.get(words[0].rsplit("/", 1)[-1]) if words else None
    if reader is None:
        return []
    return [replace(read, filtered=bool(pipe)) for read in reader(words[1:])]


def shell_reads(command: str) -> list[ShellRead]:
    """The files a command prints, in order; none when it does anything but read."""
    plain = _QUIET.sub("", command)
    if _UNSAFE.search(plain):
        return []
    found: list[ShellRead] = []
    for segment in _SEGMENTS.split(plain):
        found += _printed(segment.strip())
    return found


def working_folder(command: str) -> str | None:
    """The folder of a leading `cd DIR &&`, which relative paths in the command start from."""
    first = _SEGMENTS.split(command, maxsplit=1)[0]
    words = _words(first)
    return words[1] if len(words) == 2 and words[0] == "cd" else None
