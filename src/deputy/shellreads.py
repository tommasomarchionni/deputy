"""Which files a shell command prints (`cat`, `nl`, `sed -n`, `head`, `tail`) or edits (`sed -i`).

Used to see large reads that do not go through a read tool. Anything unusual (a redirection, a
substitution, a variable, a wildcard, a follow) means "not a plain read": the answer is then no
files, so a wrong guess can only miss a read and never claim one.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, replace

_UNSAFE = re.compile(r"[<>`]|\$\(")
_QUIET = re.compile(r"\s2>(?:/dev/null|&1)")  # hiding or joining errors changes no file
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEPARATORS = frozenset(";&|()\n")
_NOT_A_PATH = re.compile(r"[$*?\[\]{}]")
_SED_RANGE = re.compile(r"^(\d+)(?:,(\d+))?p$")
_ADDRESS = r"(?:\d+|\$|/[^/]*/)"
_SED_PRINT = re.compile(rf"^{_ADDRESS}(?:,(?:{_ADDRESS}|\+\d+))?p$")
_COUNT = re.compile(r"^-(?:n)?(\d+)$")
_CAT_OPTIONS = frozenset({"-n", "-b", "-s"})
_NL_OPTIONS = frozenset({"-ba", "-b", "a"})
_COMPOUND = frozenset({"for", "while", "until", "if", "case"})  # what runs depends on the shell
_SED_MIN_WORDS = 3  # `sed`, a script or option, a file

Stage = list[str]


@dataclass(frozen=True)
class ShellRead:
    """A file a command prints: all of it, or the part the other fields describe."""

    path: str
    first: int | None = None
    last: int | None = None  # None with `first`: to the end of the file
    from_end: int | None = None  # `tail -n N`: the last N lines
    pattern: str = ""  # `sed -n '/a/,/b/p'`: a part that only the file's content decides
    filtered: bool = False  # piped into another command, so less than this may be printed


def _pipelines(command: str) -> list[list[Stage]]:
    """The command split into pipelines and their stages, quotes respected; [] if unparsable."""
    lexer = shlex.shlex(
        command.replace("\\\n", " "), posix=True, punctuation_chars="".join(sorted(_SEPARATORS))
    )
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    pipelines: list[list[Stage]] = [[[]]]
    try:
        for token in lexer:
            if token == "|":  # noqa: S105
                pipelines[-1].append([])
            elif token and set(token) <= _SEPARATORS:
                pipelines.append([[]])
            else:
                pipelines[-1][-1].append(token)
    except ValueError:
        return []
    return pipelines


def _program(stage: Stage) -> Stage:
    """The stage without its leading `NAME=value` assignments."""
    index = 0
    while index < len(stage) and _ASSIGNMENT.match(stage[index]):
        index += 1
    return stage[index:]


def _files(words: list[str]) -> list[str]:
    """The words as paths; none when one is an option or only the shell could resolve it."""
    if any(w.startswith(("-", "+")) or _NOT_A_PATH.search(w) for w in words):
        return []
    return [os.path.expanduser(w) for w in words]


def _cat(args: list[str]) -> list[ShellRead]:
    return [ShellRead(f) for f in _files([a for a in args if a not in _CAT_OPTIONS])]


def _nl(args: list[str]) -> list[ShellRead]:
    return [ShellRead(f) for f in _files([a for a in args if a not in _NL_OPTIONS])]


def _sed(args: list[str]) -> list[ShellRead]:
    """Only `sed -n ADDRESSp FILE...`, which prints part of a file and changes nothing."""
    if len(args) < _SED_MIN_WORDS or args[0] != "-n":
        return []
    script, files = args[1], _files(args[2:])
    numbers = _SED_RANGE.match(script)
    if numbers:
        first = int(numbers[1])
        last = int(numbers[2]) if numbers[2] else first
        return [ShellRead(f, first=first, last=last) for f in files]
    if all(_SED_PRINT.match(part.strip()) for part in script.split(";")):
        return [ShellRead(f, pattern=script) for f in files]
    return []


def _count(args: list[str]) -> tuple[str, list[str]]:
    """(the count as written, the other words) of `head` or `tail`."""
    if args and args[0] == "-n" and len(args) > 1:
        return args[1], args[2:]
    found = _COUNT.match(args[0]) if args else None
    return (found[1], args[1:]) if found else ("10", args)


def _head(args: list[str]) -> list[ShellRead]:
    count, words = _count(args)
    return (
        [ShellRead(f, first=1, last=int(count)) for f in _files(words)] if count.isdigit() else []
    )


def _tail(args: list[str]) -> list[ShellRead]:
    count, words = _count(args)
    files = _files(words)
    if count.startswith("+") and count[1:].isdigit():
        return [ShellRead(f, first=int(count[1:])) for f in files]
    return [ShellRead(f, from_end=int(count)) for f in files] if count.isdigit() else []


_READERS: dict[str, Callable[[list[str]], list[ShellRead]]] = {
    "cat": _cat,
    "nl": _nl,
    "sed": _sed,
    "head": _head,
    "tail": _tail,
}


def _name(stage: Stage) -> str:
    return stage[0].rsplit("/", 1)[-1] if stage else ""


def _edits_in_place(stage: Stage) -> bool:
    return _name(stage) == "sed" and any(w.startswith(("-i", "--in-place")) for w in stage[1:])


def _all_stages(pipelines: list[list[Stage]]) -> list[Stage]:
    return [_program(stage) for pipeline in pipelines for stage in pipeline]


def shell_reads(command: str) -> list[ShellRead]:
    """The files a command prints, in order; none when it does anything but read."""
    plain = _QUIET.sub("", command)
    if _UNSAFE.search(plain):
        return []
    pipelines = _pipelines(plain)
    stages = _all_stages(pipelines)
    if any(_edits_in_place(stage) or _name(stage) in _COMPOUND for stage in stages):
        return []
    found: list[ShellRead] = []
    for pipeline in pipelines:
        words = _program(pipeline[0])
        reader = _READERS.get(_name(words))
        if reader:
            found += [replace(r, filtered=len(pipeline) > 1) for r in reader(words[1:])]
    return found


def shell_edits(command: str) -> list[str]:
    """The files a command edits with `sed -i` (the last word of each such stage)."""
    stages = _all_stages(_pipelines(_QUIET.sub("", command)))
    return _files([s[-1] for s in stages if _edits_in_place(s) and len(s) >= _SED_MIN_WORDS])


def working_folder(command: str) -> str | None:
    """The folder of a leading `cd DIR &&`, which relative paths in the command start from."""
    pipelines = _pipelines(command)
    words = pipelines[0][0] if pipelines else []
    is_cd = len(words) == 2 and words[0] == "cd"
    return os.path.expanduser(words[1]) if is_cd else None
