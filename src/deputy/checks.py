"""Checks the orchestrator declares and the server runs. The worker never runs anything.

Each check is a small model that knows how to evaluate itself (`evaluate`), so adding a check
means adding one class, not editing a chain of conditions.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from deputy.config import Policy
from deputy.sandbox import Sandbox, SandboxError

_COMMAND_ENV = frozenset({"PATH", "HOME", "LANG", "TMPDIR"})


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class CheckContext:
    """Everything a check may look at."""

    answer: str
    sandbox: Sandbox
    policy: Policy


def _result(name: str, problem: str = "") -> CheckResult:
    """A result that passes when there is no `problem` to report."""
    return CheckResult(name=name, passed=not problem, detail=problem)


class Contains(BaseModel):
    """Every value appears in the answer."""

    type: Literal["contains"] = "contains"
    values: list[str]

    async def evaluate(self, ctx: CheckContext) -> CheckResult:
        missing = [v for v in self.values if v.lower() not in ctx.answer.lower()]
        return _result("contains", f"missing: {missing}" if missing else "")


class Excludes(BaseModel):
    """No value appears in the answer."""

    type: Literal["excludes"] = "excludes"
    values: list[str]

    async def evaluate(self, ctx: CheckContext) -> CheckResult:
        present = [v for v in self.values if v.lower() in ctx.answer.lower()]
        # Never echo the forbidden text back: the report must not repeat a leak.
        return _result("excludes", "forbidden text present" if present else "")


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


def cited_numbers(answer: str) -> list[int]:
    """Every line number the answer cites, in either form."""
    numbers: list[int] = []
    for match in _WORD_CITATION.finditer(answer):
        parts = re.split(r"\s*[-\u2013,]\s*", match.group(1))
        numbers.extend(int(part) for part in parts if part)
    for match in _FILE_CITATION.finditer(answer):
        numbers.extend(int(group) for group in match.groups() if group)
    return numbers


class CitesLines(BaseModel):
    """Cited line numbers (at least one) all lie in min..max."""

    type: Literal["cites_lines"] = "cites_lines"
    min: int = 1
    max: int

    async def evaluate(self, ctx: CheckContext) -> CheckResult:
        cited = cited_numbers(ctx.answer)
        if not cited:
            return _result("cites_lines", "no citation")
        outside = sorted({n for n in cited if not self.min <= n <= self.max})
        return _result(
            "cites_lines", f"outside {self.min}-{self.max}: {outside}" if outside else ""
        )


class MaxChars(BaseModel):
    """The answer is at most `limit` characters."""

    type: Literal["max_chars"] = "max_chars"
    limit: int

    async def evaluate(self, ctx: CheckContext) -> CheckResult:
        length = len(ctx.answer)
        return _result("max_chars", f"{length} > {self.limit}" if length > self.limit else "")


class FileWritten(BaseModel):
    """The worker wrote `path`."""

    type: Literal["file_written"] = "file_written"
    path: str

    async def evaluate(self, ctx: CheckContext) -> CheckResult:
        name = f"file_written: {self.path}"
        try:
            exists = ctx.sandbox.resolve(self.path).is_file()
        except SandboxError as exc:
            return _result(name, str(exc))
        if not exists or self.path not in ctx.sandbox.files_written:
            return _result(name, "file was not written")
        return _result(name)


class FileContains(BaseModel):
    """File `path` contains every value."""

    type: Literal["file_contains"] = "file_contains"
    path: str
    values: list[str]

    async def evaluate(self, ctx: CheckContext) -> CheckResult:
        name = f"file_contains: {self.path}"
        try:
            text = ctx.sandbox.resolve(self.path).read_text(encoding="utf-8")
        except (SandboxError, OSError, UnicodeDecodeError) as exc:
            return _result(name, f"cannot read: {exc}")
        missing = [v for v in self.values if v not in text]
        return _result(name, f"missing: {missing}" if missing else "")


class Command(BaseModel):
    """An allow-listed argv exits 0 in the workdir."""

    type: Literal["command"] = "command"
    argv: list[str]

    async def evaluate(self, ctx: CheckContext) -> CheckResult:
        name = f"command: {' '.join(self.argv)}"
        if not self._allowed_by(ctx.policy):
            return _result(name, "command not allowed by the policy")
        env = {k: v for k, v in os.environ.items() if k in _COMMAND_ENV}
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *self.argv,
                cwd=ctx.sandbox.root,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await asyncio.wait_for(
                process.communicate(), timeout=ctx.policy.command_timeout_s
            )
        except TimeoutError:
            if process is not None:
                process.kill()
            return _result(name, "timed out")
        except OSError as exc:
            return _result(name, f"could not start: {exc.strerror}")
        if process.returncode == 0:
            return _result(name)
        return _result(name, output.decode("utf-8", "replace")[-600:] or "non-zero exit")

    def _allowed_by(self, policy: Policy) -> bool:
        return bool(self.argv) and any(
            prefix and self.argv[: len(prefix)] == prefix for prefix in policy.allowed_commands
        )


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


async def run_checks(
    checks: list[Check], answer: str, sandbox: Sandbox, policy: Policy
) -> list[CheckResult]:
    context = CheckContext(answer=answer, sandbox=sandbox, policy=policy)
    return [await check.evaluate(context) for check in checks]


def failures_message(results: list[CheckResult]) -> str:
    """The text sent back to the worker so it can correct itself."""
    lines = [f"- {r.name}: {r.detail}" for r in results if not r.passed]
    return "Your answer failed these checks. Fix them and answer again:\n" + "\n".join(lines)
