"""`digest`: one question about named files, answered with citations deputy proves.

Delegating asks the orchestrator to write a task and its checks; reading asks for a path. This
module writes both, so asking about a file costs no more effort than opening it.
"""

from __future__ import annotations

from dataclasses import dataclass

from deputy.checks import NOT_FOUND, Check, CitesFiles, MaxChars
from deputy.sandbox import Sandbox, SandboxError

MAX_FILES = 8
ANSWER_CHARS = 2_000


@dataclass(frozen=True)
class Plan:
    task: str
    checks: list[Check]


def _relative(path: str, sandbox: Sandbox) -> str:
    """The file's path inside the workdir; refuses what the worker could not read anyway."""
    resolved = sandbox.resolve(path)
    if not resolved.is_file():
        raise SandboxError(f"not a file: {path}")
    if sandbox.line_count(path) == 0:
        raise SandboxError(f"empty file: {path}")
    return resolved.relative_to(sandbox.root).as_posix()


def plan(files: list[str], question: str, sandbox: Sandbox) -> Plan:
    """The worker's task and its checks. Raises SandboxError before any model is called."""
    if not question.strip():
        raise SandboxError("the question is empty")
    if not files or len(files) > MAX_FILES:
        raise SandboxError(f"name between 1 and {MAX_FILES} files")
    named = list(dict.fromkeys(_relative(path, sandbox) for path in files))
    listing = "\n".join(f"- {path}" for path in named)
    task = (
        f"Answer the question using only these files.\n\nFiles:\n{listing}\n\n"
        f"Question: {question.strip()}\n\n"
        "Read the files, then answer. Back every fact with its source in exactly this form:\n"
        '- the fact (path/to/file.md:12 "exact words copied from that line")\n'
        "One fact per line, stated in your own words before its source, at most 8 lines, nothing "
        "else: no title, no introduction. Use the file "
        "path as listed, a single line number, and 3 to 10 words copied exactly from that line. "
        f"Answer in at most {ANSWER_CHARS} characters. If the files do not contain the "
        f"answer, write only: {NOT_FOUND}"
    )
    return Plan(task=task, checks=[CitesFiles(paths=named), MaxChars(limit=ANSWER_CHARS)])
