"""A Claude Code hook that suggests `digest` before a large read. It never blocks anything.

Before Claude Code reads a whole file larger than `HINT_BYTES`, or runs a shell command that prints
more than that (`cat`, `nl`, `sed -n`, `head`, `tail`), the hook adds one line to the model's
context: the size in tokens and the tool that can read it instead. A partial read through the read
tool, a small read, a filtered command, a missing file or any error adds nothing. The hook is
installed with the MCP server (`deputy install claude-code --apply`) and removed with
`deputy hook off`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, cast

from deputy import shellreads, tokens

# About 2,000 tokens: in real sessions about 1.5 % of reads, holding about 18 % of what was read.
HINT_BYTES = 8_000
MAX_MEASURED_BYTES = 5_000_000  # the worker's own limit: a larger file is not measured line by line
HOOK_TIMEOUT_S = 5  # the hint must never hold a read for long
HOOK_COMMAND = "deputy-hook"  # a light entry point: it runs before every read
_EVENT = "PreToolUse"
_MATCHER = "Read|Bash"
_BINARY_SUFFIXES = frozenset(
    [
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".ico",
        ".tiff",
        ".heic",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".tgz",
        ".bz2",
        ".xz",
        ".7z",
        ".mp3",
        ".mp4",
        ".wav",
        ".mov",
        ".webm",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".bin",
        ".wasm",
    ]
)


def _is_binary(path: Path) -> bool:
    """An image, archive or other binary file: the worker reads text only."""
    if path.suffix.lower() in _BINARY_SUFFIXES:
        return True
    try:
        with path.open("rb") as handle:
            return b"\0" in handle.read(1024)
    except OSError:
        return True


def _line(names: list[str], size: int) -> str:
    what = f"{names[0]} is" if len(names) == 1 else f"These {len(names)} files are"
    return (
        f"deputy: {what} about {tokens.from_chars(size):,} tokens. If you need facts rather than "
        "to edit, call `mcp__deputy__digest` with the files and your question instead of reading."
    )


def _read_tool_hint(arguments: dict[str, Any], cwd: str) -> str | None:
    if arguments.get("offset") or arguments.get("limit"):
        return None
    raw = str(arguments.get("file_path") or "")
    if not raw:
        return None
    path = Path(cwd) / raw
    size = path.stat().st_size
    if size <= HINT_BYTES or not path.is_file() or _is_binary(path):
        return None
    return _line([path.name], size)


def _printed_bytes(read: shellreads.ShellRead, path: Path) -> int:
    """How much of the file the command prints; 0 when that cannot be told beforehand."""
    if read.filtered or read.pattern or not path.is_file() or _is_binary(path):
        return 0
    size = path.stat().st_size
    if read.first is None and read.from_end is None:
        return size
    if size <= HINT_BYTES or size > MAX_MEASURED_BYTES:
        return 0
    lines = path.read_bytes().splitlines(keepends=True)
    if read.from_end is not None:
        part = lines[-read.from_end :] if read.from_end else []
    else:
        part = lines[(read.first or 1) - 1 : read.last]
    return sum(len(line) for line in part)


def _shell_hint(command: str, cwd: str) -> str | None:
    folder = Path(cwd) / (shellreads.working_folder(command) or "")
    sizes = {
        (folder / read.path): _printed_bytes(read, folder / read.path)
        for read in shellreads.shell_reads(command)
    }
    printed = {path: size for path, size in sizes.items() if size}
    total = sum(printed.values())
    return _line([path.name for path in printed], total) if total > HINT_BYTES else None


def hint(payload: dict[str, Any]) -> str | None:
    """The line to add before a large read, or None."""
    arguments = cast("dict[str, Any]", payload.get("tool_input") or {})
    cwd = str(payload.get("cwd") or ".")
    try:
        if payload.get("tool_name") == "Read":
            return _read_tool_hint(arguments, cwd)
        if payload.get("tool_name") == "Bash":
            return _shell_hint(str(arguments.get("command") or ""), cwd)
    except OSError:
        return None
    return None


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
    ours = {"type": "command", "command": HOOK_COMMAND, "timeout": HOOK_TIMEOUT_S}
    wanted = {"matcher": _MATCHER, "hooks": [ours]}
    current = [e for e in entries if _is_ours(e)]
    if current and all(e == wanted for e in current):
        return f"the read hint is already on ({target})"
    entries[:] = [e for e in entries if not _is_ours(e)]
    entries.append(wanted)
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


def refresh(path: Path | None = None) -> bool:
    """Bring a hint that is on up to this version; a hint that is off stays off. True if changed."""
    target = path or settings_path()
    try:
        entries = _entries(_load(target))
        ours = [e for e in entries if _is_ours(e)]
        if not ours or all(e.get("matcher") == _MATCHER for e in ours):
            return False
        enable(target)
    except (ValueError, OSError):
        return False
    return True


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
