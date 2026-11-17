"""The execution loop: run the worker in a sandbox, verify with checks, correct, report."""

from __future__ import annotations

import json
import time
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from deputy import runs
from deputy.backends import BackendError, ChatClient
from deputy.checks import Check, CheckResult, failures_message, run_checks
from deputy.config import Policy
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
    return f"ERROR: unknown tool '{name}'; use read_file, write_file or grep"


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
) -> Result:
    run_id = runs.new_run_id()
    started = time.monotonic()
    metrics = Metrics(model=client.backend.model)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": profile.body},
        {"role": "user", "content": task},
    ]
    answer = ""
    results: list[CheckResult] = []
    status: Status = "turn_limit"
    flags: list[str] = []

    try:
        while metrics.turns < max_turns:
            metrics.turns += 1
            reply = await client.chat(messages, TOOLS)
            metrics.tokens_in += reply.tokens_in
            metrics.tokens_out += reply.tokens_out
            metrics.model = reply.model
            if reply.tool_calls:
                messages.append(
                    {"role": "assistant", "content": reply.content, "tool_calls": reply.tool_calls}
                )
                for call in reply.tool_calls:
                    metrics.tool_calls += 1
                    function: dict[str, Any] = call.get("function") or {}
                    output = _execute(
                        sandbox, str(function.get("name", "")), str(function.get("arguments", ""))
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id", ""),
                            "content": output[:TOOL_RESULT_LIMIT],
                        }
                    )
                continue
            answer = reply.content.strip()
            results = await run_checks(checks, answer, sandbox, policy)
            if all(r.passed for r in results):
                status = "ok"
                break
            if metrics.retries >= max_retries:
                status = "checks_failed"
                flags.append("needs_frontier")
                break
            metrics.retries += 1
            messages.append({"role": "assistant", "content": answer})
            messages.append({"role": "user", "content": failures_message(results)})
        else:
            flags.append("turn_limit")
    except BackendError as exc:
        status = "error"
        answer = str(exc)
        flags.append("backend_error")

    metrics.elapsed_s = round(time.monotonic() - started, 1)
    saved = max(0, sandbox.chars_read // 4 - (len(answer) + len(task)) // 4)
    result = Result(
        run_id=run_id,
        status=status,
        answer=answer,
        files_written=list(sandbox.files_written),
        checks=results,
        flags=flags,
        metrics=metrics,
        orchestrator_tokens_saved_est=saved,
    )
    runs.record(
        {
            "event": "run",
            "run_id": run_id,
            "profile": profile.name,
            "status": status,
            "metrics": metrics.model_dump(),
            "checks": [{"name": c.name, "passed": c.passed} for c in results],
            "saved_est": saved,
        }
    )
    return result
