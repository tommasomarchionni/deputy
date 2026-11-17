"""A confined view of one directory: the only filesystem access a worker model ever gets."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_READ_CHARS = 60_000
MAX_WRITE_BYTES = 1_000_000
MAX_GREP_HITS = 50
_SECRET_NAMES = re.compile(
    r"(^\.env($|\.)|\.(pem|key|p12|pfx)$|(^|/)id_(rsa|ed25519|ecdsa)(\.pub)?$|^\.npmrc$|^\.netrc$)",
    re.IGNORECASE,
)


def is_secret_name(relative_path: str) -> bool:
    """True for files that look like secrets (by name): never readable, never copied."""
    return bool(
        _SECRET_NAMES.search(relative_path) or _SECRET_NAMES.search(Path(relative_path).name)
    )


class SandboxError(Exception):
    """A request the sandbox refuses. The message is safe to show to the model."""


@dataclass
class Sandbox:
    """Read and write files below `root`; nothing else.

    Every path is resolved with `realpath` and must stay inside `root`, so `..`, absolute paths
    and symlinks pointing outside are all refused. Secret-looking files are never readable.
    """

    root: Path
    chars_read: int = 0
    files_read: set[str] = field(default_factory=set[str])
    files_written: list[str] = field(default_factory=list[str])

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        if not self.root.is_dir():
            raise SandboxError(f"working directory does not exist: {self.root}")

    def resolve(self, path: str) -> Path:
        if not path or "\x00" in path:
            raise SandboxError("invalid path")
        raw = Path(path)
        candidate = raw if raw.is_absolute() else self.root / raw
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise SandboxError("path is outside the working directory")
        if is_secret_name(resolved.relative_to(self.root).as_posix()):
            raise SandboxError("this file is not accessible")
        return resolved

    def read_file(self, path: str, offset: int = 1, limit: int = 400) -> str:
        """Return lines `offset..offset+limit-1`, each prefixed with its 1-based line number."""
        target = self.resolve(path)
        if not target.is_file():
            raise SandboxError("not a file")
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SandboxError("not a UTF-8 text file") from exc
        lines = text.splitlines()
        total = len(lines)
        start = max(1, offset)
        end = min(total, start + max(1, limit) - 1)
        out: list[str] = []
        used = 0
        for number in range(start, end + 1):
            line = f"{number}: {lines[number - 1]}"
            if used + len(line) > MAX_READ_CHARS:
                end = number - 1
                break
            out.append(line)
            used += len(line) + 1
        self.chars_read += used
        self.files_read.add(target.relative_to(self.root).as_posix())
        footer = f"[lines {start}-{end} of {total}]"
        if end < total:
            footer += f" read more with offset={end + 1}"
        return "\n".join([*out, footer])

    def write_file(self, path: str, content: str) -> str:
        target = self.resolve(path)
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            raise SandboxError("content too large")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        rel = target.relative_to(self.root).as_posix()
        if rel not in self.files_written:
            self.files_written.append(rel)
        return f"wrote {rel} ({len(content)} chars)"

    def grep(self, pattern: str, path: str = ".") -> str:
        """Regex search, like `grep -rn`, without a shell. Returns `file:line: text` hits."""
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise SandboxError(f"invalid pattern: {exc}") from exc
        base = self.resolve(path)
        files = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
        hits: list[str] = []
        for file in files:
            rel = file.relative_to(self.root).as_posix()
            if any(part.startswith(".git") or part == "node_modules" for part in file.parts):
                continue
            if is_secret_name(rel):
                continue
            try:
                lines = file.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(lines, start=1):
                if regex.search(line):
                    hits.append(f"{rel}:{number}: {line[:200]}")
                    if len(hits) >= MAX_GREP_HITS:
                        return "\n".join([*hits, f"[stopped at {MAX_GREP_HITS} hits]"])
        return "\n".join(hits) if hits else "no matches"
