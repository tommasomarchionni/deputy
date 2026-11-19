"""The MCP server: six small tools. Schemas are paid by the orchestrator on every turn, so they
are kept short on purpose."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from deputy import (
    digest as digests,
)
from deputy import (
    jobs as background,
)
from deputy import (
    lessons,
    localagent,
    model_profiles,
    project_memory,
    router,
    runs,
    small_input,
    stats,
    tokens,
    updates,
)
from deputy.agent import Outcome, Result, outcome_of, run_task, task_class
from deputy.backends import ChatClient
from deputy.checks import Check, CheckSpec, to_check
from deputy.config import Backend, Config, ConfigError, Policy, load_config
from deputy.model_profiles import ModelProfile
from deputy.profiles import Profile, load_profiles
from deputy.sandbox import Sandbox, SandboxError

DEFAULT_RETRIES = 1  # one correction round with the exact failure
INSTRUCTIONS = (
    "Saves your context: a cheaper model reads files for you. Before you read a file over about "
    "8 KB, or several files, to answer a question, summarise, compare or find facts, call `digest` "
    "with the files and the question instead of reading them. If its status is ok, work from the "
    "answer: deputy proved that each quoted source is on its line, not that the model understood "
    "it, so read the cited line before you act on a fact. If the status is not ok, read the files "
    "yourself. Read directly a file you are about to edit, a short file, or what one search "
    "answers. For other work with your own checks, use `delegate`; for a well-defined code "
    "change with tests, `delegate` with agent=true does it on the local model and returns a patch."
)


@dataclass(frozen=True)
class _Delegation:
    """Everything one delegation needs, validated before any work starts."""

    config: Config
    profile: Profile
    sandbox: Sandbox
    routes: list[router.Candidate]  # the backends to try, best first
    checks: list[Check]
    notes: str


def _prepare(
    workdir: str,
    profile: str,
    checks: list[CheckSpec] | None,
    backend: str | None,
    ready_checks: list[Check] | None = None,
) -> _Delegation:
    """Raises ConfigError, SandboxError or ValidationError with a message fit for the caller."""
    config = load_config()
    profiles = load_profiles(Path(workdir))
    chosen = profiles.get(profile)
    if chosen is None:
        raise ConfigError(f"unknown profile '{profile}'; known: {', '.join(profiles)}")
    return _Delegation(
        config=config,
        profile=chosen,
        sandbox=Sandbox(Path(workdir)),
        routes=_routes(config, backend),
        checks=[*(ready_checks or []), *(to_check(c) for c in checks or [])],
        notes=project_memory.prompt_notes(
            project_memory.load(project_memory.repo_root(Path(workdir)))
        ),
    )


def _routes(config: Config, backend: str | None) -> list[router.Candidate]:
    """One backend when it is named or routing is off; otherwise the router's order."""
    if backend:
        return [router.Candidate(config.backend(backend), f"{backend} (named by the caller)")]
    if config.routing == "off":
        default = config.backend()
        return [router.Candidate(default, f"{default.name} (routing is off)")]
    return router.candidates(config, model_profiles.load_profile)


def _setup_error(run_id: str, message: str) -> Result:
    return Result(run_id=run_id, status="error", answer=message, flags=["setup_error"])


def _with_run_budget(policy: Policy, measured: ModelProfile | None) -> Policy:
    """The policy with the time budget this model needs (see `model_profiles.run_budget`)."""
    budget = model_profiles.run_budget(policy.run_budget_s, measured)
    return policy.model_copy(update={"run_budget_s": budget})


def _turn_limit(prepared: _Delegation, explicit: int | None, measured: ModelProfile | None) -> int:
    """The turn limit: what the caller asked for, else the profile's, else what was measured for
    this model, else the default."""
    return explicit or prepared.profile.max_turns or model_profiles.suggested_max_turns(measured)


async def _answers(backend: Backend) -> bool:
    """Whether the backend answers a quick probe (a down backend is skipped, not waited for)."""
    quick = backend.model_copy(update={"timeout_s": router.PROBE_TIMEOUT_S})
    client = ChatClient(quick)
    try:
        return (await client.probe()).ok
    except ConfigError:
        return False
    finally:
        await client.aclose()


@dataclass(frozen=True)
class _Request:
    task: str
    max_turns: int | None
    request_tokens: int
    run_id: str


async def _run_on(
    prepared: _Delegation, candidate: router.Candidate, route: str, args: _Request
) -> Result:
    backend = candidate.backend
    measured = model_profiles.load_profile(backend)
    client = ChatClient(backend)
    try:
        return await run_task(
            client=client,
            profile=prepared.profile,
            task=args.task,
            sandbox=prepared.sandbox,
            checks=prepared.checks,
            policy=_with_run_budget(prepared.config.policy, measured),
            max_turns=_turn_limit(prepared, args.max_turns, measured),
            max_retries=DEFAULT_RETRIES
            if prepared.profile.max_retries is None
            else prepared.profile.max_retries,
            lessons=lessons.applicable(prepared.profile.name, prepared.checks),
            request_tokens=args.request_tokens,
            notes=prepared.notes,
            run_id=args.run_id,
            route=route,
        )
    finally:
        await client.aclose()


async def _delegate(prepared: _Delegation, args: _Request) -> Result:
    """Run the task on the first backend that answers (a backend error moves on to the next one),
    and flag a kind of task that has not paid off in past runs."""
    advice = stats.advice_for_class(task_class(prepared.profile.name, prepared.checks))
    several = len(prepared.routes) > 1
    skipped: list[str] = []
    result: Result | None = None
    for candidate in prepared.routes:
        if several and not await _answers(candidate.backend):
            skipped.append(f"{candidate.backend.name} not answering")
            continue
        route = "; ".join([*skipped, f"chose {candidate.why}"])
        result = await _run_on(prepared, candidate, route, args)
        if "backend_error" not in result.flags:
            break
        skipped.append(f"{candidate.backend.name} backend error")
    if result is None:
        return _setup_error(args.run_id, "no backend answers: " + "; ".join(skipped))
    if advice:
        result.flags.append(stats.ADVICE_FLAG)
    small = small_input.advice(args.task, prepared.sandbox)
    if small:
        result.flags.append(small)
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
    pending = background.Jobs()

    async def launch(prepared: _Delegation, request: _Request, wait: bool) -> Outcome:
        work = _delegate(prepared, request)
        if wait:
            return outcome_of(await work)
        if not pending.has_room():
            work.close()
            message = f"{background.MAX_ACTIVE} jobs are already running"
            return outcome_of(_setup_error(request.run_id, message))
        return outcome_of(pending.submit(request.run_id, prepared.sandbox, work))

    async def start_agent(prepared: _Delegation, task: str, workdir: str, run_id: str) -> Outcome:
        backend = prepared.routes[0].backend
        if backend.kind != "local":
            return outcome_of(_setup_error(run_id, "the local agent needs a local backend"))
        if not pending.has_room():
            message = f"{background.MAX_ACTIVE} jobs are already running"
            return outcome_of(_setup_error(run_id, message))
        try:
            await localagent.require_context(backend)
        except ConfigError as exc:
            return outcome_of(_setup_error(run_id, str(exc)))
        policy = prepared.config.policy
        work = localagent.run(task, Path(workdir), backend, prepared.checks, policy, run_id)
        return outcome_of(pending.submit(run_id, prepared.sandbox, work))

    @server.tool(
        description=(
            "Run a task on a worker in the absolute `workdir`, verified by `checks`. wait=False: "
            "then `collect`. agent=True: a local agent changes code in a private copy and returns "
            "a patch."
        )
    )
    async def delegate(  # noqa: PLR0913, PLR0917
        task: str,
        workdir: str,
        profile: str = "helper",
        checks: list[CheckSpec] | None = None,
        backend: str | None = None,
        max_turns: int | None = None,
        wait: bool = True,
        agent: bool = False,
    ) -> Outcome:
        run_id = runs.new_run_id()
        try:
            prepared = _prepare(workdir, profile, checks, backend)
        except ValidationError as exc:
            detail = "; ".join(str(e["msg"]) for e in exc.errors())
            return outcome_of(_setup_error(run_id, f"invalid check: {detail}"))
        except (ConfigError, SandboxError) as exc:
            return outcome_of(_setup_error(run_id, str(exc)))
        request = {
            "task": task,
            "workdir": workdir,
            "profile": profile,
            "backend": backend,
            "max_turns": max_turns,
            "checks": [c.model_dump(exclude_none=True) for c in checks or []],
        }
        if agent:
            return await start_agent(prepared, task, workdir, run_id)
        args = _Request(task, max_turns, tokens.estimate_value(request), run_id)
        return await launch(prepared, args, wait)

    @server.tool(  # the result shape is already described by `delegate`
        structured_output=False,
        description=(
            "Ask about files in the absolute `workdir` instead of reading them: every line of the "
            "answer cites a proven file line."
        ),
    )
    async def digest(files: list[str], question: str, workdir: str) -> Outcome:
        run_id = runs.new_run_id()
        try:
            plan = digests.plan(files, question, Sandbox(Path(workdir)))
            prepared = _prepare(workdir, "helper", None, None, plan.checks)
        except (ConfigError, SandboxError) as exc:
            return outcome_of(_setup_error(run_id, str(exc)))
        cost = tokens.estimate_value({"files": files, "question": question, "workdir": workdir})
        return await launch(prepared, _Request(plan.task, None, cost, run_id), wait=True)

    @server.tool(structured_output=False)  # the result shape is already described by `delegate`
    async def collect(run_id: str, cancel: bool = False) -> Outcome:
        """Result of a background delegation (waits 20 s); cancel=True stops it."""
        return outcome_of(await pending.collect(run_id, cancel=cancel))

    @server.tool()
    async def status() -> dict[str, str]:
        """Backends and whether they answer."""
        try:
            return await _backend_status(load_config())
        except ConfigError as exc:
            return {"error": str(exc)}

    @server.tool(structured_output=False)
    def profiles(workdir: str | None = None) -> dict[str, str]:
        """Available profiles."""
        found = load_profiles(Path(workdir) if workdir else None)
        return {name: p.description for name, p in found.items()}

    @server.tool(structured_output=False)
    def feedback(run_id: str, verdict: Literal["accepted", "rejected"], reason: str = "") -> str:
        """Report whether a result was good."""
        runs.record(
            {"event": "feedback", "run_id": run_id, "verdict": verdict, "reason": reason[:500]}
        )
        return "recorded"

    return server
