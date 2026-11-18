"""Ask a question about a file that does not fit in the model's context, by reading it in parts.

The file is cut into chunks of whole lines; each chunk goes to the worker model on its own, with
the question, in a fresh conversation that fits the context; the findings of the chunks are
merged, each under the range of lines it came from. Every line keeps the number it has in the
original file, so what the worker cites at the end is true of the whole file.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from deputy.backends import BackendError, ChatClient
from deputy.sandbox import Sandbox, SandboxError

CHUNK_CHARS = 24_000  # about 6,000 tokens: a chunk, the question and the answer fit small contexts
MAX_CHUNKS = 12
FINDINGS_PER_CHUNK_CHARS = 1_500
FINDINGS_TOTAL_CHARS = 12_000
SCAN_BUDGET_S = 600.0  # a scan on a slow local model can take minutes per part; it must end
NOTHING = "NONE"

_now = time.monotonic  # a seam, so a test can move the clock without touching everyone's

_SYSTEM = (
    "You read one part of a file and report what is relevant to a question. Use only the text you "
    "are given. Reply with one finding per line, each starting with the line number it comes from "
    f"(`N: the fact`). If nothing in this part is relevant, reply exactly {NOTHING}."
)


@dataclass(frozen=True)
class Chunk:
    first: int  # 1-based line number in the original file
    last: int
    text: str  # the lines, each prefixed with its original number


def split(lines: list[str], max_chars: int = CHUNK_CHARS) -> list[Chunk]:
    """Cut a file into chunks of whole lines, numbered as in the original. A line longer than the
    limit is cut so that one chunk can never exceed it."""
    chunks: list[Chunk] = []
    current: list[str] = []
    size = 0
    first = 1
    for number, raw in enumerate(lines, start=1):
        row = f"{number}: {raw[: max_chars - 16]}"
        if current and size + len(row) + 1 > max_chars:
            chunks.append(Chunk(first, number - 1, "\n".join(current)))
            current, size, first = [], 0, number
        current.append(row)
        size += len(row) + 1
    if current:
        chunks.append(Chunk(first, len(lines), "\n".join(current)))
    return chunks


def _prompt(question: str, path: str, chunk: Chunk) -> list[dict[str, str]]:
    body = (
        f"Question: {question}\n\nLines {chunk.first}-{chunk.last} of {path} "
        f"(each line starts with its number):\n{chunk.text}"
    )
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": body}]


async def scan_file(client: ChatClient, sandbox: Sandbox, path: str, question: str) -> str:
    """The merged findings of every chunk of `path` for `question`, or an error for the worker."""
    try:
        lines = sandbox.read_all_lines(path)
    except SandboxError as exc:
        return f"ERROR: {exc}"
    chunks = split(lines)
    if len(chunks) > MAX_CHUNKS:
        return (
            f"ERROR: {path} has {len(lines)} lines, too many to scan "
            f"({len(chunks)} parts, at most {MAX_CHUNKS}); use grep to narrow it down"
        )
    findings: list[str] = []
    deadline = _now() + SCAN_BUDGET_S
    stopped = ""
    for chunk in chunks:
        if _now() > deadline:
            stopped = f" The scan stopped at line {chunk.first}: its time budget was used up."
            break
        try:
            reply = await client.chat(_prompt(question, path, chunk))
        except BackendError as exc:
            return f"ERROR: the scan stopped at lines {chunk.first}-{chunk.last}: {exc}"
        answer = reply.content.strip()
        if answer and answer.upper() != NOTHING:
            findings.append(
                f"[lines {chunk.first}-{chunk.last}]\n{answer[:FINDINGS_PER_CHUNK_CHARS]}"
            )
    head = f"Scanned {path}: {len(lines)} lines in {len(chunks)} part(s).{stopped}"
    if not findings:
        return f"{head} Nothing relevant to the question was found."
    return (head + "\n" + "\n".join(findings))[:FINDINGS_TOTAL_CHARS]
