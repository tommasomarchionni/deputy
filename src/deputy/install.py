"""Wire deputy into an agent. Printing is the default; --apply does it for you."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

AGENTS = ("claude-code", "vscode", "cursor", "codex", "generic")
COMMAND = "deputy"
MARK_START = "<!-- deputy:start -->"
MARK_END = "<!-- deputy:end -->"


def instructions_text() -> str:
    return (resources.files("deputy") / "instructions.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class InstructionTarget:
    """Where an agent reads instructions from, and how the file must look."""

    path: str
    frontmatter: str | None  # None: a shared file, so deputy only owns a marked block in it
    note: str


_DESCRIPTION = (
    "Delegate small, checkable, read-heavy tasks (reading long files or logs, extraction, "
    "summaries, translations, first drafts of tests) to a cheaper model with the deputy MCP "
    "tools, to save tokens. Use when a task is input-heavy and its answer is short."
)

INSTRUCTION_TARGETS: dict[str, InstructionTarget] = {
    "claude": InstructionTarget(
        ".claude/skills/deputy/SKILL.md",
        f"---\nname: deputy\ndescription: {_DESCRIPTION}\n---\n",
        "a Claude Code skill: loaded only when relevant, so it costs no tokens otherwise",
    ),
    "claude-md": InstructionTarget("CLAUDE.md", None, "always loaded by Claude Code"),
    "copilot": InstructionTarget(
        ".github/instructions/deputy.instructions.md",
        '---\napplyTo: "**"\n---\n',
        "a GitHub Copilot instructions file",
    ),
    "copilot-repo": InstructionTarget(
        ".github/copilot-instructions.md", None, "GitHub Copilot repository instructions"
    ),
    "cursor": InstructionTarget(
        ".cursor/rules/deputy.mdc",
        f"---\ndescription: {_DESCRIPTION}\nalwaysApply: false\n---\n",
        "a Cursor rule the agent attaches when relevant",
    ),
    "agents": InstructionTarget("AGENTS.md", None, "read by Codex, Copilot and many other agents"),
}


def render_instructions(target: str) -> str:
    """The guide as the target agent expects it (front matter included for owned files)."""
    spec = INSTRUCTION_TARGETS[target]
    body = instructions_text().strip() + "\n"
    return f"{spec.frontmatter}\n{body}" if spec.frontmatter else body


def apply_instructions(target: str, project: Path) -> str:
    """Write the guide where `target` reads it: owned files whole, shared files as a block."""
    spec = INSTRUCTION_TARGETS[target]
    path = project / spec.path
    if spec.frontmatter is None:
        return write_instructions(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_instructions(target), encoding="utf-8")
    return f"wrote {path}"


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
