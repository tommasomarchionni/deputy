"""The execution loop: run the worker in a sandbox, verify with checks, correct, report."""

from __future__ import annotations

import json
import time
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from deputy import runs, scan, tokens
from deputy.backends import BackendError, ChatClient, Reply
from deputy.checks import Check, CheckResult, failures_message, run_checks
from deputy.config import Policy
from deputy.lessons import Lesson, prompt_addendum
from deputy.profiles import Profile
from deputy.sandbox import Sandbox, SandboxError

TOOL_RESULT_LIMIT = 24_000

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file; lines come numbered. Page with offset and limit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "description": "first line, 1-based"},
                    "limit": {"type": "integer", "description": "number of lines"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the full content.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_file",
            "description": (
                "Ask a question about a file too large to read: it is read in parts and what is "
                "relevant is reported with the line numbers of the original file."
            ),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "question": {"type": "string"}},
                "required": ["path", "question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Regex search in a file or folder; returns file:line: text.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
]

Status = Literal["ok", "checks_failed", "error", "turn_limit"]


class Metrics(BaseModel):
    model: str = ""
    turns: int = 0
    tool_calls: int = 0
    retries: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_s: float = 0.0


class Result(BaseModel):
    run_id: str
    status: Status
    answer: str = ""
    files_written: list[str] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list[CheckResult])
    flags: list[str] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    # Estimated tokens the orchestrator would have spent reading the files itself (chars / 4)
    # minus what this result costs it. A rough, labelled estimate, never a promise.
    orchestrator_tokens_saved_est: int = 0


def _execute(sandbox: Sandbox, name: str, raw_args: str) -> str:  # noqa: PLR0911
    try:
        parsed: Any = json.loads(raw_args) if raw_args else {}
        if not isinstance(parsed, dict):
            raise ValueError
        arguments = cast("dict[str, Any]", parsed)
    except ValueError:
        return "ERROR: arguments must be a JSON object"
    try:
        if name == "read_file":
            return sandbox.read_file(
                str(arguments["path"]),
                int(arguments.get("offset") or 1),
                int(arguments.get("limit") or 400),
            )
        if name == "write_file":
            return sandbox.write_file(str(arguments["path"]), str(arguments["content"]))
        if name == "grep":
            return sandbox.grep(str(arguments["pattern"]), str(arguments.get("path") or "."))
    except SandboxError as exc:
        return f"ERROR: {exc}"
    except (KeyError, TypeError, ValueError):
        return "ERROR: missing or invalid arguments"
    return f"ERROR: unknown tool '{name}'; use read_file, write_file, grep or scan_file"


def task_class(profile: str, checks: list[Check]) -> str:
    """Group runs that ask for the same kind of work: the profile and the kinds of checks."""
    kinds = sorted({check.type for check in checks})
    return f"{profile}:{'+'.join(kinds) if kinds else 'no-checks'}"


class AgentRun:
    """One delegated task: converse with the worker, run its tools, verify, correct, report."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        client: ChatClient,
        profile: Profile,
        task: str,
        sandbox: Sandbox,
        checks: list[Check],
        policy: Policy,
        max_turns: int,
        max_retries: int,
        source: str,
        lessons: list[Lesson],
        request_tokens: int,
    ) -> None:
        self._request_tokens = request_tokens
        self._read_tokens = 0
        self._cost_tokens = 0
        self._source = source
        self._lessons = lessons
        self._client = client
        self._profile = profile
        self._task = task
        self._sandbox = sandbox
        self._checks = checks
        self._policy = policy
        self._max_turns = max_turns
        self._max_retries = max_retries
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": profile.body + prompt_addendum(lessons)},
            {"role": "user", "content": task},
        ]
        self._metrics = Metrics(model=client.backend.model)
        self._answer = ""
        self._check_results: list[CheckResult] = []
        self._flags: list[str] = []
        self._status: Status = "turn_limit"

    async def execute(self) -> Result:
        run_id = runs.new_run_id()
        started = time.monotonic()
        try:
            await self._converse()
        except BackendError as exc:
            self._status = "error"
            self._answer = str(exc)
            self._flags.append("backend_error")
        self._metrics.elapsed_s = round(time.monotonic() - started, 1)
        result = self._build_result(run_id)
        self._record(result)
        return result

    async def _converse(self) -> None:
        while self._metrics.turns < self._max_turns:
            self._metrics.turns += 1
            reply = await self._client.chat(self._messages, TOOLS)
            self._count_usage(reply)
            if reply.tool_calls:
                await self._run_tool_calls(reply)
            elif await self._verify(reply.content.strip()):
                return
        self._flags.append("turn_limit")

    def _count_usage(self, reply: Reply) -> None:
        self._metrics.tokens_in += reply.tokens_in
        self._metrics.tokens_out += reply.tokens_out
        self._metrics.model = reply.model

    async def _run_tool_calls(self, reply: Reply) -> None:
        self._messages.append(
            {"role": "assistant", "content": reply.content, "tool_calls": reply.tool_calls}
        )
        for call in reply.tool_calls:
            self._metrics.tool_calls += 1
            function: dict[str, Any] = call.get("function") or {}
            output = await self._call_tool(
                str(function.get("name", "")), str(function.get("arguments", ""))
            )
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": output[:TOOL_RESULT_LIMIT],
                }
            )

    async def _call_tool(self, name: str, raw_args: str) -> str:
        """Run one tool call. `scan_file` needs the model, so it lives here; the rest is pure."""
        if name != "scan_file":
            return _execute(self._sandbox, name, raw_args)
        try:
            arguments = json.loads(raw_args or "{}")
            return await scan.scan_file(
                self._client, self._sandbox, str(arguments["path"]), str(arguments["question"])
            )
        except (ValueError, KeyError, TypeError):
            return "ERROR: scan_file needs a path and a question"

    async def _verify(self, answer: str) -> bool:
        """Run the checks on a final answer. True when the conversation is over."""
        self._answer = answer
        self._check_results = await run_checks(self._checks, answer, self._sandbox, self._policy)
        if all(r.passed for r in self._check_results):
            self._status = "ok"
            return True
        if self._metrics.retries >= self._max_retries:
            self._status = "checks_failed"
            self._flags.append("needs_frontier")
            return True
        self._metrics.retries += 1
        self._messages.append({"role": "assistant", "content": answer})
        self._messages.append({"role": "user", "content": failures_message(self._check_results)})
        return False

    def _build_result(self, run_id: str) -> Result:
        """The result, with what delegating cost the orchestrator and what it spared it.

        Cost: the request it sent plus the result it reads (serialised). Spared: what the worker
        read on its behalf. Both use the same estimator (see `deputy.tokens`); the difference may
        be negative, which is the point: a delegation that does not pay says so."""
        result = Result(
            run_id=run_id,
            status=self._status,
            answer=self._answer,
            files_written=list(self._sandbox.files_written),
            checks=self._check_results,
            flags=self._flags,
            metrics=self._metrics,
        )
        self._read_tokens = tokens.from_chars(self._sandbox.chars_read)
        self._cost_tokens = self._request_tokens + tokens.estimate(result.model_dump_json())
        result.orchestrator_tokens_saved_est = self._read_tokens - self._cost_tokens
        return result

    def _record(self, result: Result) -> None:
        runs.record(
            {
                "event": "run",
                "run_id": result.run_id,
                "profile": self._profile.name,
                "source": self._source,
                "lessons": [lesson.id for lesson in self._lessons],
                "task_class": task_class(self._profile.name, self._checks),
                "status": result.status,
                "flags": result.flags,
                "metrics": result.metrics.model_dump(),
                "checks": [{"name": c.name, "passed": c.passed} for c in result.checks],
                "read_est": self._read_tokens,
                "cost_est": self._cost_tokens,
                "saved_est": result.orchestrator_tokens_saved_est,
            }
        )


async def run_task(  # noqa: PLR0913
    *,
    client: ChatClient,
    profile: Profile,
    task: str,
    sandbox: Sandbox,
    checks: list[Check],
    policy: Policy,
    max_turns: int = 8,
    max_retries: int = 1,
    source: str = "mcp",
    lessons: list[Lesson] | None = None,
    request_tokens: int | None = None,
) -> Result:
    """Delegate `task` to the worker behind `client` and return the verified result.

    `source` says who asked: "mcp" for real use, "bench" for the benchmark, so that statistics can
    tell them apart."""
    return await AgentRun(
        client=client,
        profile=profile,
        task=task,
        sandbox=sandbox,
        checks=checks,
        policy=policy,
        max_turns=max_turns,
        max_retries=max_retries,
        source=source,
        lessons=lessons or [],
        request_tokens=tokens.estimate(task) if request_tokens is None else request_tokens,
    ).execute()
