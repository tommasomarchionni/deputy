"""Checks the orchestrator declares and the server runs. The worker never runs anything.

Each check is a small model that knows how to evaluate itself (`evaluate`), so adding a check
means adding one class, not editing a chain of conditions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import jsonschema
from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator
from referencing import Registry
from referencing.exceptions import Unresolvable

from deputy.config import Policy
from deputy.sandbox import Sandbox, SandboxError, read_lines
from deputy.schema import COMPACT

_COMMAND_ENV = frozenset({"PATH", "HOME", "LANG", "TMPDIR"})


class CheckResult(BaseModel):
    model_config = COMPACT

    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class CheckContext:
    """Everything a check may look at."""

    answer: str
    sandbox: Sandbox
    policy: Policy
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    """Added to a `command` check's environment: the local agent's copy needs its own."""
    wrapper: list[str] | None = None
    """A sandbox for `command` checks that takes the place of the policy's. A command that runs
    inside it needs no allow-list: it can do no more than the agent's own confined shell."""


def _result(name: str, problem: str = "") -> CheckResult:
    """A result that passes when there is no `problem` to report."""
    return CheckResult(name=name, passed=not problem, detail=problem)


def appears(value: str, text: str) -> bool:
    """`value` is in `text` as a whole, ignoring case: "12" is not found in "12,599" or "112",
    "log" is not found in "logs". Edges that are not letters or digits ("#100", "30 days") match
    as written."""
    if not value:
        return True
    pattern = re.escape(value)
    if value[0].isalnum():
        pattern = r"(?<!\w)" + (r"(?<!\d[.,])" if value[0].isdigit() else "") + pattern
    if value[-1].isalnum():
        pattern += r"(?!\w)" + (r"(?![.,]\d)" if value[-1].isdigit() else "")
    return re.search(pattern, text, re.IGNORECASE) is not None


class Contains(BaseModel):
    """Every value appears in the answer."""

    type: Literal["contains"] = "contains"
    values: list[str]

    async def evaluate(self, ctx: CheckContext) -> CheckResult:
        missing = [v for v in self.values if not appears(v, ctx.answer)]
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
    """Cited line numbers (at least one) all lie in min..max. With `path` and no `max`, max is the
    file's current number of lines, so the check does not go stale when the file grows."""

    type: Literal["cites_lines"] = "cites_lines"
    min: int = 1
    max: int | None = None
    path: str | None = None

    @model_validator(mode="after")
    def _has_an_upper_bound(self) -> CitesLines:
        if self.max is None and not self.path:
            raise ValueError("cites_lines needs `max` or the `path` of the file cited")
        return self

    def _upper(self, ctx: CheckContext) -> int | str:
        """The upper bound, or a reason it cannot be known."""
        if self.max is not None:
            return self.max
        try:
            return ctx.sandbox.line_count(str(self.path))
        except SandboxError as exc:
            return f"cannot read {self.path}: {exc}"

    async def evaluate(self, ctx: CheckContext) -> CheckResult:
        cited = cited_numbers(ctx.answer)
        if not cited:
            return _result("cites_lines", "no citation")
        upper = self._upper(ctx)
        if isinstance(upper, str):
            return _result("cites_lines", upper)
        outside = sorted({n for n in cited if not self.min <= n <= upper})
        return _result("cites_lines", f"outside {self.min}-{upper}: {outside}" if outside else "")


_QUOTE = re.compile(
    r'"(?P<a>[^"\n]{8,200})"|\u201c(?P<b>[^\u201d\n]{8,200})\u201d|`(?P<c>[^`\n]{8,200})`'
    r"|(?<!\w)'(?P<d>[^'\n]{8,200})'(?!\w)"
)
_REF = (
    r"(?:\blines?\b|\brighe?\b|\bl\.|\br\.)\s*(?P<{i}a>\d+)(?:\s*[-\u2013]\s*(?P<{i}b>\d+))?"
    r"|\b(?P<{i}c>\d+)\s*:"
    r"|[A-Za-z_][\w./-]*\.[A-Za-z0-9]+:(?P<{i}d>\d+)(?:[-\u2013](?P<{i}e>\d+))?"
)
_REF_BEFORE = re.compile(f"(?:{_REF.format(i='p')})[\\s:,(\\-\u2013]*$", re.IGNORECASE)
_REF_AFTER = re.compile(f"^[\\s,(\\-\u2013]*(?:{_REF.format(i='n')})", re.IGNORECASE)
_NEIGHBOUR_CHARS = 40


def _span(match: re.Match[str] | None, prefix: str) -> tuple[int, int] | None:
    """The line or line range named by a reference match, or None."""
    if match is None:
        return None
    groups = match.groupdict()
    first = groups.get(f"{prefix}a") or groups.get(f"{prefix}c") or groups.get(f"{prefix}d")
    last = groups.get(f"{prefix}b") or groups.get(f"{prefix}e") or first
    return (int(first), int(last)) if first and last else None


def quoted_citations(answer: str) -> list[tuple[int, int, str]]:
    """Every quoted passage that sits right next to a line reference: (first, last, text).

    The reference may come before ('line 12: "text"', '12: "text"', 'a.txt:12 "text"') or after
    ('"text" (line 12)'). A quote with no reference next to it is not a citation."""
    pairs: list[tuple[int, int, str]] = []
    for quote in _QUOTE.finditer(answer):
        text = next(g for g in quote.groups() if g)
        before = answer[max(0, quote.start() - _NEIGHBOUR_CHARS) : quote.start()]
        after = answer[quote.end() : quote.end() + _NEIGHBOUR_CHARS]
        span = _span(_REF_BEFORE.search(before), "p") or _span(_REF_AFTER.search(after), "n")
        if span:
            pairs.append((span[0], span[1], text))
    return pairs


def _normal(text: str) -> str:
    """Compare quotes the way a reader would: case and runs of whitespace do not matter."""
    return " ".join(text.split()).casefold()


class CitesText(BaseModel):
    """Every quoted passage next to a line number is really on that line of `path`."""

    type: Literal["cites_text"] = "cites_text"
    path: str

    async def evaluate(self, ctx: CheckContext) -> CheckResult:
        name = f"cites_text: {self.path}"
        try:
            lines = read_lines(ctx.sandbox.resolve(self.path))
        except SandboxError as exc:
            return _result(name, f"cannot read: {exc}")
        pairs = quoted_citations(ctx.answer)
        if not pairs:
            return _result(name, 'no quoted citation (expected: line 12: "the exact words")')
        wrong: list[str] = []
        for first, last, text in pairs:
            where = f"{first}" if first == last else f"{first}-{last}"
            window = lines[first - 1 : last] if 1 <= first <= last <= len(lines) else []
            if not any(_normal(text) in _normal(line) for line in window):
                wrong.append(f"line {where} does not contain {text[:40]!r}")
        return _result(name, "; ".join(wrong))


_NAMED_CITATION = re.compile(
    r"(?<![\w/.:@-])(?P<name>[A-Za-z_.][\w./-]*\.[A-Za-z0-9]+):(?P<first>\d+)(?:[-\u2013](?P<last>\d+))?"
)


def _matching(name: str, paths: list[str]) -> list[str]:
    """The listed paths a cited name can mean: the path itself, or its ending."""
    exact = [p for p in paths if p == name]
    return exact or [p for p in paths if p.endswith(f"/{name}")]


_TRAILING_QUOTE = re.compile(r'^[\s,:)\-\u2013]*"(?P<words>.+)"\s*\)?[.,;]?\s*$')
_MIN_QUOTE_CHARS = 8
_MIN_FACT_WORDS = 2


def _quote_near(answer: str, start: int, end: int) -> str | None:
    """The quoted words after a citation (they may hold quotes themselves), or right before it."""
    rest = answer[end:].split("\n", 1)[0]
    closing = _TRAILING_QUOTE.match(rest)
    if closing:
        return closing["words"] if len(closing["words"]) >= _MIN_QUOTE_CHARS else None
    before = answer[max(0, start - 240) : start].rstrip(" ,:(-\u2013")
    preceding = [q for q in _QUOTE.finditer(before) if q.end() == len(before)]
    return next(g for g in preceding[-1].groups() if g) if preceding else None


def _loose(text: str) -> str:
    """Compare quotes as a reader would: escapes and Markdown markers are not words."""
    return _normal(text.replace("\\", "").replace("`", "").replace("**", ""))


def _quote_is_on(quote: str, text: list[str], first: int, last: int) -> bool:
    """The words are on the cited lines; they may run on to the line before or after (wrapped
    text), but words found only on a neighbouring line do not count."""
    before = _loose(text[first - 2]) if first > 1 else ""
    cited = _loose(" ".join(text[first - 1 : last]))
    after = _loose(text[last]) if last < len(text) else ""
    whole = f"{before} {cited} {after}"
    words = _loose(quote)
    start = len(before) + 1
    found = whole.find(words)
    while found >= 0:
        if found < start + len(cited) and found + len(words) > start:
            return True
        found = whole.find(words, found + 1)
    return False


class CitesFiles(BaseModel):
    """Every line of the answer states a fact and cites a listed file, or another file of the work
    folder, and a real line of it, with words quoted from that line. Nothing stands without its
    source, and no source without a fact."""

    type: Literal["cites_files"] = "cites_files"
    paths: list[str]

    def _problem(self, answer: str, match: re.Match[str], lines: dict[str, list[str]]) -> str:
        cited = match["name"]
        found = _matching(cited, self.paths) or ([cited] if cited in lines else [])
        if len(found) != 1:
            return f"{cited} is not one of the files" if not found else f"{cited} is ambiguous"
        first = int(match["first"])
        last = int(match["last"] or first)
        text = lines[found[0]]
        if not 1 <= first <= last <= len(text):
            return f"{cited}:{first} is outside the file ({len(text)} lines)"
        quote = _quote_near(answer, match.start(), match.end())
        if quote is None:
            return f'{cited}:{first} has no quoted words (expected: {cited}:{first} "exact words")'
        if not _quote_is_on(quote, text, first, last):
            return f"{cited}:{first} does not contain {quote[:40]!r}"
        return ""

    async def evaluate(self, ctx: CheckContext) -> CheckResult:
        try:
            lines = {p: read_lines(ctx.sandbox.resolve(p)) for p in self.paths}
        except SandboxError as exc:
            return _result("cites_files", f"cannot read: {exc}")
        cited = list(_NAMED_CITATION.finditer(ctx.answer))
        if not cited:
            return _result("cites_files", 'no citation (expected: file.md:12 "the exact words")')
        # a fact from another file of the folder is as true as one from a listed file: prove it
        for name in {match["name"] for match in cited}:
            if not _matching(name, self.paths):
                with contextlib.suppress(SandboxError):
                    lines[name] = read_lines(ctx.sandbox.resolve(name))
        problems = [p for m in cited if (p := self._problem(ctx.answer, m, lines))]
        bare = [
            line.strip()[:40]
            for line in ctx.answer.splitlines()
            if line.strip() and not _NAMED_CITATION.search(line)
        ]
        problems += [f"no source for: {line!r}" for line in bare]
        problems += [
            f"no fact before the source: {line.strip()[:40]!r}"
            for line in ctx.answer.splitlines()
            if (found := _NAMED_CITATION.search(line))
            and len(re.findall(r"\w+", _QUOTE.sub("", line[: found.start()]))) < _MIN_FACT_WORDS
        ]
        return _result("cites_files", "; ".join(problems[:5]))


_FENCE = re.compile(r"```(?:json)?\s*\n(?P<body>.*?)```", re.DOTALL | re.IGNORECASE)
_MAX_SCHEMA_ERRORS = 5


def _json_text(text: str) -> str:
    """The JSON of an answer: the first fenced block if there is one, else the text itself."""
    fenced = _FENCE.search(text)
    return (fenced.group("body") if fenced else text).strip()


def _location(error: jsonschema.ValidationError) -> str:
    parts = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path)
    return f"${parts}"


class JsonSchema(BaseModel):
    """The answer (or the file `path`) is JSON that validates against `json_schema`."""

    type: Literal["json_schema"] = "json_schema"
    json_schema: dict[str, Any]
    path: str | None = None

    @field_validator("json_schema")
    @classmethod
    def _is_a_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            jsonschema.Draft202012Validator.check_schema(value)
        except jsonschema.SchemaError as exc:
            raise ValueError(f"not a valid JSON schema: {exc.message}") from exc
        return value

    async def evaluate(self, ctx: CheckContext) -> CheckResult:
        name = f"json_schema: {self.path}" if self.path else "json_schema"
        try:
            source = (
                ctx.sandbox.resolve(self.path).read_text(encoding="utf-8")
                if self.path
                else ctx.answer
            )
        except (SandboxError, OSError, UnicodeDecodeError) as exc:
            return _result(name, f"cannot read: {exc}")
        document: Any
        try:
            document = json.loads(_json_text(source))
        except ValueError as exc:
            return _result(name, f"not valid JSON: {exc}")
        # An empty registry: jsonschema would otherwise download a `$ref` to a URL by itself.
        validator = jsonschema.Draft202012Validator(self.json_schema, registry=Registry())
        try:
            errors: list[jsonschema.ValidationError] = sorted(
                validator.iter_errors(document),  # pyright: ignore[reportUnknownMemberType]
                key=lambda e: list(e.absolute_path),
            )
        except Unresolvable as exc:  # a $ref that cannot be resolved offline (nothing is fetched)
            return _result(name, f"the schema refers to something unavailable: {exc}")
        shown = [f"{_location(e)}: {e.message}" for e in errors[:_MAX_SCHEMA_ERRORS]]
        more = (
            f" (and {len(errors) - _MAX_SCHEMA_ERRORS} more)"
            if len(errors) > _MAX_SCHEMA_ERRORS
            else ""
        )
        return _result(name, "; ".join(shown) + more if errors else "")


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


def _confined(argv: list[str], ctx: CheckContext) -> list[str]:
    """The command, behind the policy's wrapper (if any), with {workdir} filled in."""
    wrapper = ctx.policy.command_wrapper if ctx.wrapper is None else ctx.wrapper
    prefix = [part.replace("{workdir}", str(ctx.sandbox.root)) for part in wrapper]
    return [*prefix, *argv]


class Command(BaseModel):
    """An allow-listed argv exits 0 in the workdir."""

    type: Literal["command"] = "command"
    argv: list[str]

    async def evaluate(self, ctx: CheckContext) -> CheckResult:
        name = f"command: {' '.join(self.argv)}"
        if ctx.wrapper is None and not self.allowed_by(ctx.policy):
            return _result(name, "command not allowed by the policy")
        env = {k: v for k, v in os.environ.items() if k in _COMMAND_ENV} | dict(ctx.env)
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *_confined(self.argv, ctx),
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
        except asyncio.CancelledError:
            if process is not None:
                process.kill()  # a cancelled run leaves no process behind
            raise
        except OSError as exc:
            return _result(name, f"could not start: {exc.strerror}")
        if process.returncode == 0:
            return _result(name)
        return _result(name, output.decode("utf-8", "replace")[-600:] or "non-zero exit")

    def allowed_by(self, policy: Policy) -> bool:
        return bool(self.argv) and any(
            prefix and self.argv[: len(prefix)] == prefix for prefix in policy.allowed_commands
        )


Check = Annotated[
    Contains
    | CitesLines
    | CitesText
    | CitesFiles
    | JsonSchema
    | MaxChars
    | FileWritten
    | FileContains
    | Excludes
    | Command,
    Field(discriminator="type"),
]


class CheckSpec(BaseModel):
    model_config = COMPACT
    """One proof run on the answer: `type` picks the kind, the other fields are its arguments."""

    type: Literal[
        "contains",
        "excludes",
        "cites_lines",
        "cites_text",
        "json_schema",
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
    json_schema: dict[str, Any] | None = None


_CHECK_ADAPTER: TypeAdapter[Check] = TypeAdapter(Check)


def to_check(spec: CheckSpec) -> Check:
    """Validate a flat spec into its strict form. Raises pydantic.ValidationError."""
    return _CHECK_ADAPTER.validate_python(spec.model_dump(exclude_none=True))


async def run_checks(  # noqa: PLR0913
    checks: list[Check],
    answer: str,
    sandbox: Sandbox,
    policy: Policy,
    *,
    env: Mapping[str, str] | None = None,
    wrapper: list[str] | None = None,
) -> list[CheckResult]:
    context = CheckContext(
        answer=answer, sandbox=sandbox, policy=policy, env=env or {}, wrapper=wrapper
    )
    return [await check.evaluate(context) for check in checks]


def failures_message(results: list[CheckResult]) -> str:
    """The text sent back to the worker so it can correct itself."""
    lines = [f"- {r.name}: {r.detail}" for r in results if not r.passed]
    return "Your answer failed these checks. Fix them and answer again:\n" + "\n".join(lines)
