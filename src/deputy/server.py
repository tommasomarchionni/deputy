"""The MCP server: four small tools. Schemas are paid by the orchestrator on every turn, so they
are kept short on purpose."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from deputy import lessons, model_profiles, project_memory, runs, stats, tokens, updates
from deputy.agent import Result, run_task, task_class
from deputy.backends import ChatClient
from deputy.checks import Check, CheckSpec, to_check
from deputy.config import Config, ConfigError, load_config
from deputy.model_profiles import ModelProfile
from deputy.profiles import Profile, load_profiles
from deputy.sandbox import Sandbox, SandboxError

DEFAULT_RETRIES = 1  # one correction round with the exact failure
INSTRUCTIONS = "Delegate small, checkable, read-heavy tasks to another model. Use `delegate`."


@dataclass(frozen=True)
class _Delegation:
    """Everything one delegation needs, validated before any work starts."""

    config: Config
    profile: Profile
    sandbox: Sandbox
    client: ChatClient
    checks: list[Check]
    measured: ModelProfile | None
    notes: str


def _prepare(
    workdir: str, profile: str, checks: list[CheckSpec] | None, backend: str | None
) -> _Delegation:
    """Raises ConfigError, SandboxError or ValidationError with a message fit for the caller."""
    config = load_config()
    profiles = load_profiles(Path(workdir))
    chosen = profiles.get(profile)
    if chosen is None:
        raise ConfigError(f"unknown profile '{profile}'; known: {', '.join(profiles)}")
    chosen_backend = config.backend(backend)
    return _Delegation(
        config=config,
        profile=chosen,
        sandbox=Sandbox(Path(workdir)),
        client=ChatClient(chosen_backend),
        checks=[to_check(c) for c in checks or []],
        measured=model_profiles.load_profile(chosen_backend),
        notes=project_memory.prompt_notes(
            project_memory.load(project_memory.repo_root(Path(workdir)))
        ),
    )


def _setup_error(run_id: str, message: str) -> Result:
    return Result(run_id=run_id, status="error", answer=message, flags=["setup_error"])


def _turn_limit(prepared: _Delegation, explicit: int | None) -> int:
    """The turn limit: what the caller asked for, else the profile's, else what was measured for
    this model, else the default."""
    return (
        explicit
        or prepared.profile.max_turns
        or model_profiles.suggested_max_turns(prepared.measured)
    )


async def _delegate(
    prepared: _Delegation, task: str, max_turns: int | None, request_tokens: int
) -> Result:
    """Run the task, and flag a kind of task that has not paid off in past runs."""
    advice = stats.advice_for_class(task_class(prepared.profile.name, prepared.checks))
    try:
        result = await run_task(
            client=prepared.client,
            profile=prepared.profile,
            task=task,
            sandbox=prepared.sandbox,
            checks=prepared.checks,
            policy=prepared.config.policy,
            max_turns=_turn_limit(prepared, max_turns),
            max_retries=DEFAULT_RETRIES
            if prepared.profile.max_retries is None
            else prepared.profile.max_retries,
            lessons=lessons.applicable(prepared.profile.name, prepared.checks),
            request_tokens=request_tokens,
            notes=prepared.notes,
        )
    finally:
        await prepared.client.aclose()
    if advice:
        result.flags.append(stats.ADVICE_FLAG)
    return result


async def _backend_status(config: Config) -> dict[str, str]:
    out: dict[str, str] = {}
    for backend in config.backends:
        client = ChatClient(backend)
        try:
            probe = await client.probe()
        finally:
            await client.aclose()
        out[backend.name] = ("up: " if probe.ok else "down: ") + probe.detail
        if probe.ok:
            measured = model_profiles.load_profile(backend)
            out[f"{backend.name} profile"] = model_profiles.describe(measured, probe.models)
    usage = stats.usage_line()
    if usage:
        out["usage"] = usage
    advice = stats.advice_lines()
    if advice:
        out["advice"] = " | ".join(advice)
    if config.auto_update != "off":
        info = await asyncio.to_thread(updates.check)
        if info.available:
            out["update"] = f"{info.latest} available (installed {info.current}): deputy update"
    return out


def build_server() -> MCPServer:
    server = MCPServer("deputy", instructions=INSTRUCTIONS)

    @server.tool()
    async def delegate(  # noqa: PLR0913, PLR0917
        task: str,
        workdir: str,
        profile: str = "helper",
        checks: list[CheckSpec] | None = None,
        backend: str | None = None,
        max_turns: int | None = None,
    ) -> Result:
        """Run a task on a worker model in `workdir` (absolute path). Verified by `checks`."""
        run_id = runs.new_run_id()
        try:
            prepared = _prepare(workdir, profile, checks, backend)
        except ValidationError as exc:
            detail = "; ".join(str(e["msg"]) for e in exc.errors())
            return _setup_error(run_id, f"invalid check: {detail}")
        except (ConfigError, SandboxError) as exc:
            return _setup_error(run_id, str(exc))
        request = {
            "task": task,
            "workdir": workdir,
            "profile": profile,
            "backend": backend,
            "max_turns": max_turns,
            "checks": [c.model_dump(exclude_none=True) for c in checks or []],
        }
        return await _delegate(prepared, task, max_turns, tokens.estimate_value(request))

    @server.tool()
    async def status() -> dict[str, str]:
        """Configured backends and whether they answer."""
        try:
            return await _backend_status(load_config())
        except ConfigError as exc:
            return {"error": str(exc)}

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
