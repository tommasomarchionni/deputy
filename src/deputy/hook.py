"""A Claude Code hook that suggests delegating a large read. It never blocks anything.

Before Claude Code reads a whole file larger than `HINT_BYTES`, the hook adds one line to the
model's context: the file's size in tokens and that `delegate` can read it instead. A read of part
of a file (offset or limit), a small file, a missing file or any error adds nothing. The hook is
installed with the MCP server (`deputy install claude-code --apply`) and removed with
`deputy hook off`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, cast

from deputy import tokens

# About 2,000 tokens: in real sessions about 1.5 % of reads, holding about 18 % of what was read.
HINT_BYTES = 8_000
HOOK_COMMAND = "deputy-hook"  # a light entry point: it runs before every read
_EVENT = "PreToolUse"


def hint(payload: dict[str, Any]) -> str | None:
    """The line to add before a large whole-file read, or None."""
    if payload.get("tool_name") != "Read":
        return None
    arguments = cast("dict[str, Any]", payload.get("tool_input") or {})
    if arguments.get("offset") or arguments.get("limit"):
        return None
    raw = str(arguments.get("file_path") or "")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(str(payload.get("cwd") or ".")) / path
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= HINT_BYTES or not path.is_file():
        return None
    return (
        f"deputy: {path.name} is about {tokens.from_chars(size):,} tokens. If you need facts from "
        "it rather than to edit it, the deputy `delegate` tool can read it for you."
    )


def run(stdin_text: str) -> str:
    """What the hook prints: a JSON object that adds context, or nothing. Never raises."""
    try:
        payload = json.loads(stdin_text)
        line = hint(cast("dict[str, Any]", payload)) if isinstance(payload, dict) else None
    except (ValueError, OSError):
        return ""
    if line is None:
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": _EVENT, "additionalContext": line}})


def settings_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "settings.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data: Any = json.loads(path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return cast("dict[str, Any]", data)


def _entries(settings: dict[str, Any]) -> list[dict[str, Any]]:
    hooks = cast("dict[str, Any]", settings.setdefault("hooks", {}))
    return cast("list[dict[str, Any]]", hooks.setdefault(_EVENT, []))


def _is_ours(entry: dict[str, Any]) -> bool:
    inner = cast("list[dict[str, Any]]", entry.get("hooks") or [])
    return any(h.get("command") in (HOOK_COMMAND, "deputy hook read-hint") for h in inner)


def _write(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.deputy-tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def enable(path: Path | None = None) -> str:
    """Add the hook to Claude Code's user settings; keeps everything else as it is."""
    target = path or settings_path()
    settings = _load(target)
    entries = _entries(settings)
    if any(_is_ours(e) for e in entries):
        return f"the read hint is already on ({target})"
    entries.append({"matcher": "Read", "hooks": [{"type": "command", "command": HOOK_COMMAND}]})
    _write(target, settings)
    return (
        f"read hint on: Claude Code will suggest delegating large reads ({target}); "
        "off with `deputy hook off`"
    )


def disable(path: Path | None = None) -> str:
    target = path or settings_path()
    settings = _load(target)
    entries = _entries(settings)
    kept = [e for e in entries if not _is_ours(e)]
    if len(kept) == len(entries):
        return f"the read hint is already off ({target})"
    hooks = cast("dict[str, Any]", settings["hooks"])
    if kept:
        hooks[_EVENT] = kept
    else:
        del hooks[_EVENT]
    if not hooks:
        del settings["hooks"]
    _write(target, settings)
    return f"read hint off ({target}); on again with `deputy hook on`"


def is_enabled(path: Path | None = None) -> bool:
    try:
        return any(_is_ours(e) for e in _entries(_load(path or settings_path())))
    except (ValueError, OSError):
        return False


def main() -> int:
    """`deputy-hook`: read the hook payload on stdin, print the hint if any. Always exits 0."""
    out = run(sys.stdin.read())
    if out:
        print(out)
    return 0
