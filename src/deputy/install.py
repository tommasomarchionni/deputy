"""Wire deputy into an agent. Printing is the default; --apply does it for you."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from deputy import hook

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
    "Delegate reading to a cheaper model with the deputy MCP tools, to save tokens: summaries, "
    "inventories, facts with their lines from long files, big logs, git history. Use when a task "
    "needs reading more than a few KB and its answer is short; not for lookups or searches."
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


def user_skill_path() -> Path:
    """Claude Code's user-level skill for deputy: it applies in every project."""
    return hook.settings_path().parent / "skills" / "deputy" / "SKILL.md"


def install_user_skill(path: Path | None = None) -> str:
    """Write the delegation guide as a user-level Claude Code skill."""
    target = path or user_skill_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_instructions("claude"), encoding="utf-8")
    return f"skill installed: {target} (kept current by deputy)"


def refresh_user_skill(path: Path | None = None) -> bool:
    """Rewrite the user-level skill if it exists and is out of date. Never creates it, never
    raises: it runs when the server starts, after an update. True when it rewrote the file."""
    target = path or user_skill_path()
    try:
        if not target.is_file():
            return False
        wanted = render_instructions("claude")
        if target.read_text(encoding="utf-8") == wanted:
            return False
        target.write_text(wanted, encoding="utf-8")
    except OSError:
        return False
    return True


def stale_project_skill(project: Path) -> str | None:
    """A warning when this folder has its own copy of the skill that differs from the guide."""
    copy = project / INSTRUCTION_TARGETS["claude"].path
    try:
        if copy.is_file() and copy.read_text(encoding="utf-8") != render_instructions("claude"):
            return (
                f"skill: {copy} is an older copy of the delegation guide; delete it (the "
                "user-level skill from `deputy install claude-code --apply` stays current) or "
                "refresh it with `deputy instructions --for claude --apply`"
            )
    except OSError:
        return None
    return None


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


def apply(agent: str, project: Path, *, read_hint: bool = True, skill: bool = True) -> str:
    """Register deputy with `agent`. Returns a one-line report; raises RuntimeError on failure.

    For Claude Code it also installs the user-level skill (the delegation guide, kept current by
    deputy) and turns on the read hint (`deputy hook`), unless told not to."""
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
        report = "registered with Claude Code (user scope)"
        if skill:
            try:
                report += "\n" + install_user_skill()
            except OSError as exc:
                report += f"\nskill not installed: {exc}"
        if read_hint:
            try:
                report += "\n" + hook.enable()
            except (ValueError, OSError) as exc:
                report += f"\nread hint not installed: {exc}"
        return report
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
