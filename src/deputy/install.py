"""Wire deputy into an agent. Printing is the default; --apply does it for you."""

from __future__ import annotations

import json
import shutil
import subprocess
from importlib import resources
from pathlib import Path
from typing import Any

AGENTS = ("claude-code", "vscode", "cursor", "codex", "generic")
COMMAND = "deputy"
MARK_START = "<!-- deputy:start -->"
MARK_END = "<!-- deputy:end -->"


def instructions_text() -> str:
    return (resources.files("deputy") / "instructions.md").read_text(encoding="utf-8")


def _json_entry(key: str, *, with_type: bool) -> dict[str, Any]:
    entry: dict[str, Any] = {"command": COMMAND, "args": ["mcp"]}
    if with_type:
        entry = {"type": "stdio", **entry}
    return {key: {"deputy": entry}}


def snippet(agent: str) -> str:
    """The configuration to add for `agent`, as text."""
    if agent == "claude-code":
        return f"claude mcp add --scope user deputy -- {COMMAND} mcp"
    if agent == "vscode":  # GitHub Copilot in VS Code
        return json.dumps(_json_entry("servers", with_type=True), indent=2)
    if agent == "cursor":
        return json.dumps(_json_entry("mcpServers", with_type=False), indent=2)
    if agent == "codex":
        return f'[mcp_servers.deputy]\ncommand = "{COMMAND}"\nargs = ["mcp"]'
    return json.dumps(_json_entry("mcpServers", with_type=False), indent=2)


def _merge_json(path: Path, key: str, with_type: bool) -> str:
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise RuntimeError(
                f"{path} is not valid JSON; fix it or add the entry by hand"
            ) from exc
        if isinstance(loaded, dict):
            data = loaded  # pyright: ignore[reportUnknownVariableType]
    servers: dict[str, Any] = data.setdefault(key, {})
    if "deputy" in servers:
        return f"already present in {path}"
    servers.update(_json_entry(key, with_type=with_type)[key])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return f"added to {path}"


def apply(agent: str, project: Path) -> str:
    """Register deputy with `agent`. Returns a one-line report; raises RuntimeError on failure."""
    if agent == "claude-code":
        claude = shutil.which("claude")
        if claude is None:
            raise RuntimeError("the `claude` CLI is not on PATH; run the printed command by hand")
        done = subprocess.run(  # noqa: S603
            [claude, "mcp", "add", "--scope", "user", "deputy", "--", COMMAND, "mcp"],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            raise RuntimeError((done.stderr or done.stdout).strip() or "claude mcp add failed")
        return "registered with Claude Code (user scope)"
    if agent == "vscode":
        return _merge_json(project / ".vscode" / "mcp.json", "servers", with_type=True)
    if agent == "cursor":
        return _merge_json(project / ".cursor" / "mcp.json", "mcpServers", with_type=False)
    raise RuntimeError(f"--apply is not supported for '{agent}'; paste the printed snippet")


def write_instructions(path: Path) -> str:
    """Insert or refresh the delegation guide in a markdown file, between markers."""
    block = f"{MARK_START}\n{instructions_text().strip()}\n{MARK_END}\n"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if MARK_START in text and MARK_END in text:
        head, _, rest = text.partition(MARK_START)
        _, _, tail = rest.partition(MARK_END)
        new = head + block.rstrip("\n") + tail
        action = "updated"
    else:
        new = text + ("\n" if text and not text.endswith("\n\n") else "") + block
        action = "added"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return f"{action} the delegation guide in {path}"
