"""The MCP server: four small tools. Schemas are paid by the orchestrator on every turn, so they
are kept short on purpose."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from deputy import runs, updates
from deputy.agent import Result, run_task
from deputy.backends import ChatClient
from deputy.checks import CheckSpec, to_check
from deputy.config import ConfigError, load_config
from deputy.profiles import load_profiles
from deputy.sandbox import Sandbox, SandboxError

INSTRUCTIONS = "Delegate small, checkable, read-heavy tasks to another model. Use `delegate`."


def build_server() -> MCPServer:
    server = MCPServer("deputy", instructions=INSTRUCTIONS)

    @server.tool()
    async def delegate(  # noqa: PLR0913, PLR0917
        task: str,
        workdir: str,
        profile: str = "helper",
        checks: list[CheckSpec] | None = None,
        backend: str | None = None,
        max_turns: int = 8,
    ) -> Result:
        """Run a task on a worker model in `workdir` (absolute path). Verified by `checks`."""
        run_id = runs.new_run_id()
        try:
            config = load_config()
            profiles = load_profiles(Path(workdir))
            chosen = profiles.get(profile)
            if chosen is None:
                raise ConfigError(f"unknown profile '{profile}'; known: {', '.join(profiles)}")
            sandbox = Sandbox(Path(workdir))
            client = ChatClient(config.backend(backend))
            strict_checks = [to_check(c) for c in checks or []]
        except ValidationError as exc:
            detail = "; ".join(str(e["msg"]) for e in exc.errors())
            return Result(
                run_id=run_id,
                status="error",
                answer=f"invalid check: {detail}",
                flags=["setup_error"],
            )
        except (ConfigError, SandboxError) as exc:
            return Result(run_id=run_id, status="error", answer=str(exc), flags=["setup_error"])
        try:
            return await run_task(
                client=client,
                profile=chosen,
                task=task,
                sandbox=sandbox,
                checks=strict_checks,
                policy=config.policy,
                max_turns=max_turns,
            )
        finally:
            await client.aclose()

    @server.tool()
    async def status() -> dict[str, str]:
        """Configured backends and whether they answer."""
        try:
            config = load_config()
        except ConfigError as exc:
            return {"error": str(exc)}
        out: dict[str, str] = {}
        for backend in config.backends:
            client = ChatClient(backend)
            try:
                ok, detail = await client.health()
            finally:
                await client.aclose()
            out[backend.name] = ("up: " if ok else "down: ") + detail
        if config.auto_update != "off":
            info = await asyncio.to_thread(updates.check)
            if info.available:
                out["update"] = f"{info.latest} available (installed {info.current}): deputy update"
        return out

    @server.tool()
    def profiles(workdir: str | None = None) -> dict[str, str]:
        """Available profiles (name: description)."""
        found = load_profiles(Path(workdir) if workdir else None)
        return {name: p.description for name, p in found.items()}

    @server.tool()
    def feedback(run_id: str, verdict: Literal["accepted", "rejected"], reason: str = "") -> str:
        """Tell deputy whether a result was good. Improves routing and profiles."""
        runs.record(
            {"event": "feedback", "run_id": run_id, "verdict": verdict, "reason": reason[:500]}
        )
        return "recorded"

    return server
