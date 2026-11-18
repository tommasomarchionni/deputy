"""Advice from the size of what a task names: a short file is cheaper to read than to delegate."""

from __future__ import annotations

import re

from deputy import tokens
from deputy.sandbox import Sandbox, SandboxError

SMALL_INPUT_FLAG = "small_input"
# Below this many tokens of input, the request and the result cost about as much as reading it.
SMALL_INPUT_TOKENS = 1_000
_PATH_LIKE = re.compile(r"[\w./-]+\.\w{1,8}")


def named_input_tokens(task: str, sandbox: Sandbox) -> int | None:
    """Estimated tokens of the existing files the task names, or None when it names none."""
    names = {m.group(0).strip(".,:;") for m in _PATH_LIKE.finditer(task)}
    total = 0
    found = False
    for name in sorted(names):
        try:
            path = sandbox.resolve(name)
        except SandboxError:
            continue
        if path.is_file():
            found = True
            total += tokens.from_chars(path.stat().st_size)
    return total if found else None


def advice(task: str, sandbox: Sandbox) -> str | None:
    """The flag, with the estimate, when every file the task names is small; else None."""
    size = named_input_tokens(task, sandbox)
    if size is None or size >= SMALL_INPUT_TOKENS:
        return None
    return f"{SMALL_INPUT_FLAG}: the files named are about {size} tokens; read them yourself"
