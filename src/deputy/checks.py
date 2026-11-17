"""Checks the orchestrator declares and the server runs. The worker never runs anything."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from deputy.config import Policy
from deputy.sandbox import Sandbox, SandboxError


class Contains(BaseModel):
    """Every value must appear in the answer (case-insensitive): required facts."""

    type: Literal["contains"] = "contains"
    values: list[str]


class CitesLines(BaseModel):
    """Every line number the answer cites ('line 12', 'lines 12-14', 'righe 12-14', 'l. 12')
    must fall inside [min, max]. At least one citation is required."""

    type: Literal["cites_lines"] = "cites_lines"
    min: int = 1
    max: int


class MaxChars(BaseModel):
    """The answer must stay short: this is what protects the orchestrator's tokens."""

    type: Literal["max_chars"] = "max_chars"
    limit: int


class FileWritten(BaseModel):
    type: Literal["file_written"] = "file_written"
    path: str


class Command(BaseModel):
    """Run an allow-listed command in the working directory (no shell). Must exit 0."""

    type: Literal["command"] = "command"
    argv: list[str]


Check = Annotated[
    Contains | CitesLines | MaxChars | FileWritten | Command, Field(discriminator="type")
]


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


_CITATION = re.compile(
    r"(?:\blines?\b|\brighe?\b|\bl\.|\br\.)\s*((?:\d+\s*[-\u2013,]\s*)*\d+)", re.IGNORECASE
)


def _cited_numbers(answer: str) -> list[int]:
    numbers: list[int] = []
    for match in _CITATION.finditer(answer):
        parts = re.split(r"\s*[-\u2013,]\s*", match.group(1))
        numbers.extend(int(p) for p in parts if p)
    return numbers


def _allowed(argv: list[str], policy: Policy) -> bool:
    return any(argv[: len(prefix)] == prefix for prefix in policy.allowed_commands if prefix)


async def _run_command(argv: list[str], sandbox: Sandbox, policy: Policy) -> CheckResult:
    name = f"command: {' '.join(argv)}"
    if not argv or not _allowed(argv, policy):
        return CheckResult(name=name, passed=False, detail="command not allowed by the policy")
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "TMPDIR"}}
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=sandbox.root,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(process.communicate(), timeout=policy.command_timeout_s)
    except TimeoutError:
        if process is not None:
            process.kill()
        return CheckResult(name=name, passed=False, detail="timed out")
    except OSError as exc:
        return CheckResult(name=name, passed=False, detail=f"could not start: {exc.strerror}")
    tail = out.decode("utf-8", "replace")[-600:]
    return CheckResult(name=name, passed=process.returncode == 0, detail=tail)


async def run_checks(
    checks: list[Check], answer: str, sandbox: Sandbox, policy: Policy
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for check in checks:
        if isinstance(check, Contains):
            missing = [v for v in check.values if v.lower() not in answer.lower()]
            results.append(
                CheckResult(
                    name="contains",
                    passed=not missing,
                    detail=f"missing: {missing}" if missing else "",
                )
            )
        elif isinstance(check, CitesLines):
            cited = _cited_numbers(answer)
            bad = sorted({n for n in cited if not check.min <= n <= check.max})
            if not cited:
                results.append(CheckResult(name="cites_lines", passed=False, detail="no citation"))
            else:
                results.append(
                    CheckResult(
                        name="cites_lines",
                        passed=not bad,
                        detail=f"outside {check.min}-{check.max}: {bad}" if bad else "",
                    )
                )
        elif isinstance(check, MaxChars):
            ok = len(answer) <= check.limit
            results.append(
                CheckResult(
                    name="max_chars",
                    passed=ok,
                    detail="" if ok else f"{len(answer)} > {check.limit}",
                )
            )
        elif isinstance(check, FileWritten):
            try:
                exists = sandbox.resolve(check.path).is_file()
            except SandboxError as exc:
                results.append(CheckResult(name="file_written", passed=False, detail=str(exc)))
                continue
            results.append(
                CheckResult(
                    name=f"file_written: {check.path}",
                    passed=exists and check.path in sandbox.files_written,
                    detail="" if exists else "file was not written",
                )
            )
        else:
            results.append(await _run_command(check.argv, sandbox, policy))
    return results


def failures_message(results: list[CheckResult]) -> str:
    lines = [f"- {r.name}: {r.detail}" for r in results if not r.passed]
    return "Your answer failed these checks. Fix them and answer again:\n" + "\n".join(lines)
