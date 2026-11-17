"""Checks the orchestrator declares and the server runs. The worker never runs anything."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from deputy.config import Policy
from deputy.sandbox import Sandbox, SandboxError


class Contains(BaseModel):
    """Every value appears in the answer."""

    type: Literal["contains"] = "contains"
    values: list[str]


class CitesLines(BaseModel):
    """Cited line numbers (at least one) all lie in min..max."""

    type: Literal["cites_lines"] = "cites_lines"
    min: int = 1
    max: int


class MaxChars(BaseModel):
    """The answer is at most `limit` characters."""

    type: Literal["max_chars"] = "max_chars"
    limit: int


class FileWritten(BaseModel):
    """The worker wrote `path`."""

    type: Literal["file_written"] = "file_written"
    path: str


class FileContains(BaseModel):
    """File `path` contains every value."""

    type: Literal["file_contains"] = "file_contains"
    path: str
    values: list[str]


class Excludes(BaseModel):
    """No value appears in the answer."""

    type: Literal["excludes"] = "excludes"
    values: list[str]


class Command(BaseModel):
    """An allow-listed argv exits 0 in the workdir."""

    type: Literal["command"] = "command"
    argv: list[str]


Check = Annotated[
    Contains | CitesLines | MaxChars | FileWritten | FileContains | Excludes | Command,
    Field(discriminator="type"),
]


class CheckSpec(BaseModel):
    """What the orchestrator sends: one flat shape, so the tool schema stays small.
    `type` is contains|excludes|cites_lines|max_chars|file_written|file_contains|command."""

    type: Literal[
        "contains",
        "excludes",
        "cites_lines",
        "max_chars",
        "file_written",
        "file_contains",
        "command",
    ]
    values: list[str] | None = None
    min: int | None = None
    max: int | None = None
    limit: int | None = None
    path: str | None = None
    argv: list[str] | None = None


_CHECK_ADAPTER: TypeAdapter[Check] = TypeAdapter(Check)


def to_check(spec: CheckSpec) -> Check:
    """Validate a flat spec into its strict form. Raises pydantic.ValidationError."""
    return _CHECK_ADAPTER.validate_python(spec.model_dump(exclude_none=True))


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


# "line 12", "lines 12-14", "righe 12", "l. 12" ...
_WORD_CITATION = re.compile(
    r"(?:\blines?\b|\brighe?\b|\bl\.|\br\.)\s*((?:\d+\s*[-\u2013,]\s*)*\d+)", re.IGNORECASE
)
# ... and the file:line form of grep and compilers: "long.log:540", "src/a.py:12-14".
# The name must start at a word boundary and contain a dot, so timestamps (12:09:00) and
# URLs (http://host:8080) are not read as citations.
_FILE_CITATION = re.compile(
    r"(?<![\w/.:@-])[A-Za-z_][\w./-]*\.[A-Za-z0-9]+:(\d+)(?:[-\u2013](\d+))?"
)


def _cited_numbers(answer: str) -> list[int]:
    numbers: list[int] = []
    for match in _WORD_CITATION.finditer(answer):
        parts = re.split(r"\s*[-\u2013,]\s*", match.group(1))
        numbers.extend(int(p) for p in parts if p)
    for match in _FILE_CITATION.finditer(answer):
        numbers.extend(int(g) for g in match.groups() if g)
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


def _file_contains(check: FileContains, sandbox: Sandbox) -> CheckResult:
    name = f"file_contains: {check.path}"
    try:
        text = sandbox.resolve(check.path).read_text(encoding="utf-8")
    except (SandboxError, OSError, UnicodeDecodeError) as exc:
        return CheckResult(name=name, passed=False, detail=f"cannot read: {exc}")
    missing = [v for v in check.values if v not in text]
    return CheckResult(
        name=name, passed=not missing, detail=f"missing: {missing}" if missing else ""
    )


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
        elif isinstance(check, Excludes):
            present = [v for v in check.values if v.lower() in answer.lower()]
            results.append(
                CheckResult(
                    name="excludes",
                    passed=not present,
                    detail="forbidden text present" if present else "",
                )
            )
        elif isinstance(check, FileContains):
            results.append(_file_contains(check, sandbox))
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
