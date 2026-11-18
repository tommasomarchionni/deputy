"""A confined view of one directory: the only filesystem access a worker model ever gets."""

from __future__ import annotations

import os
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

import regex

MAX_READ_CHARS = 60_000
MAX_FILE_BYTES = 5_000_000
LARGE_FILE_CHARS = 80_000  # above this, a read points the worker at scan_file
MAX_WRITE_BYTES = 1_000_000
MAX_GREP_HITS = 50
MAX_PATTERN_CHARS = 200
MAX_LINE_CHARS = 2_000  # a line is cut here before matching: a long line must not become a bomb
MATCH_TIMEOUT_S = 0.05  # one line may take this long (a catastrophic pattern is abandoned)
GREP_BUDGET_S = 5.0  # one whole search may take this long
_SECRET_FILES = re.compile(
    r"(^\.env($|\.)|\.(pem|key|p12|pfx|keystore)$|^id_(rsa|ed25519|ecdsa|dsa)(\.pub)?$"
    r"|^\.(npmrc|netrc|pypirc|htpasswd|git-credentials)$|^credentials(\.|$)|^secrets?\.)",
    re.IGNORECASE,
)
_SECRET_DIRS = frozenset({".git", ".ssh", ".aws", ".gnupg", ".kube", ".docker"})


def is_secret_name(relative_path: str) -> bool:
    """True for files that look like secrets (by name or folder): never readable, never copied."""
    parts = Path(relative_path).parts
    if any(part.lower() in _SECRET_DIRS for part in parts):
        return True
    return bool(parts) and bool(_SECRET_FILES.search(parts[-1]))


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
    # What each written file held before (None: it did not exist), so a cancelled run can be undone.
    _before: dict[str, bytes | None] = field(default_factory=dict[str, bytes | None])
    _made_dirs: list[Path] = field(default_factory=list[Path])

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
        lines = _read_text(target).splitlines()
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
        if sum(len(line) + 1 for line in lines) > LARGE_FILE_CHARS:
            footer += "; the file is large: scan_file(path, question) can read all of it in parts"
        return "\n".join([*out, footer])

    def read_all_lines(self, path: str) -> list[str]:
        """Every line of a file (for a scan in parts), counted as read by the worker."""
        target = self.resolve(path)
        lines = _read_text(target).splitlines()
        self.chars_read += sum(len(line) + 1 for line in lines)
        self.files_read.add(target.relative_to(self.root).as_posix())
        return lines

    def write_file(self, path: str, content: str) -> str:
        target = self.resolve(path)
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            raise SandboxError("content too large")
        rel = target.relative_to(self.root).as_posix()
        if rel not in self._before:
            self._before[rel] = _snapshot(target)
        self._make_dirs(target.parent)
        _write_text(target, content)
        if rel not in self.files_written:
            self.files_written.append(rel)
        return f"wrote {rel} ({len(content)} chars)"

    def _make_dirs(self, folder: Path) -> None:
        """Create `folder`, remembering the folders that did not exist before."""
        missing = [
            p for p in (folder, *folder.parents) if not p.exists() and self.root in p.parents
        ]
        folder.mkdir(parents=True, exist_ok=True)
        self._made_dirs.extend(reversed(missing))

    def rollback(self) -> int:
        """Put every file the worker wrote back as it was (or remove it); returns how many."""
        undone = 0
        for rel, before in self._before.items():
            target = self.root / rel
            try:
                if before is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(before)
                undone += 1
            except OSError:
                continue
        for folder in reversed(self._made_dirs):
            try:
                folder.rmdir()  # only if empty: anything else in it is not ours to remove
            except OSError:
                continue
        self._before.clear()
        self._made_dirs.clear()
        self.files_written.clear()
        return undone

    def _contained(self, candidate: Path) -> Path | None:
        """The real path of `candidate` if it is inside the root and not a secret, else None.

        A symlink inside the folder that points outside is skipped here, so a search never reads
        what `resolve` would refuse."""
        try:
            return self.resolve(str(candidate))
        except SandboxError:
            return None

    def grep(self, pattern: str, path: str = ".") -> str:
        """Regex search, like `grep -rn`, without a shell. Returns `file:line: text` hits."""
        if len(pattern) > MAX_PATTERN_CHARS:
            raise SandboxError(f"pattern too long (over {MAX_PATTERN_CHARS} characters)")
        try:
            compiled = regex.compile(pattern)
        except regex.error as exc:
            raise SandboxError(f"invalid pattern: {exc}") from exc
        deadline = time.monotonic() + GREP_BUDGET_S
        base = self.resolve(path)
        files = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
        hits: list[str] = []
        for candidate in files:
            file = self._contained(candidate)
            if file is None or "node_modules" in file.parts:
                continue
            rel = file.relative_to(self.root).as_posix()
            try:
                lines = _read_text(file).splitlines()
            except SandboxError:
                continue
            counted = False
            for number, line in enumerate(lines, start=1):
                if time.monotonic() > deadline:
                    return "\n".join([*hits, "[search stopped: its time budget was used up]"])
                if _matches(compiled, line):
                    if not counted:  # the orchestrator would have had to read this file
                        self.chars_read += sum(len(row) + 1 for row in lines)
                        counted = True
                    hits.append(f"{rel}:{number}: {line[:200]}")
                    if len(hits) >= MAX_GREP_HITS:
                        return "\n".join([*hits, f"[stopped at {MAX_GREP_HITS} hits]"])
        return "\n".join(hits) if hits else "no matches"


def _open_regular(
    path: Path, flags: int, *, single_link: bool = False
) -> tuple[int, os.stat_result]:
    """Open `path` without following a symlink at the last component and check, on the open file
    descriptor, that it is a regular file (and, if asked, not hard-linked). The checks are made on
    the descriptor, so nothing can change between the check and the use. A FIFO or device (which
    could block) is refused; the descriptor is closed on any refusal."""
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags, 0o644)
    except OSError as exc:
        raise SandboxError("cannot open this file") from exc
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or (single_link and info.st_nlink > 1):
        os.close(descriptor)
        raise SandboxError("not a plain file (or it is hard-linked)")
    return descriptor, info


def read_lines(path: Path) -> list[str]:
    """The lines of a text file inside the sandbox, read with the same safety as `read_file`."""
    return _read_text(path).splitlines()


def _read_text(path: Path) -> str:
    """Read a UTF-8 text file; files larger than the cap are refused."""
    descriptor, info = _open_regular(path, os.O_RDONLY)
    with os.fdopen(descriptor, "rb") as handle:
        if info.st_size > MAX_FILE_BYTES:
            raise SandboxError(f"file too large ({info.st_size} bytes); use grep for a search")
        try:
            return handle.read().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SandboxError("not a UTF-8 text file") from exc


def _snapshot(path: Path) -> bytes | None:
    """The current bytes of a file about to be overwritten, or None if there is no such file."""
    if not path.exists():
        return None
    descriptor, info = _open_regular(path, os.O_RDONLY, single_link=True)
    with os.fdopen(descriptor, "rb") as handle:
        if info.st_size > MAX_FILE_BYTES:
            raise SandboxError(f"file too large to overwrite ({info.st_size} bytes)")
        return handle.read()


def _write_text(path: Path, content: str) -> None:
    """Write a file, never through a hard link: a second name for a file that may live outside."""
    descriptor, _ = _open_regular(path, os.O_WRONLY | os.O_CREAT, single_link=True)
    with os.fdopen(descriptor, "wb") as handle:
        handle.truncate(0)
        handle.write(content.encode("utf-8"))


def _matches(compiled: regex.Pattern[str], line: str) -> bool:
    """Whether the pattern matches the (cut) line; a pattern that takes too long is refused."""
    try:
        return compiled.search(line[:MAX_LINE_CHARS], timeout=MATCH_TIMEOUT_S) is not None
    except TimeoutError as exc:
        raise SandboxError(
            "this pattern is too slow on this file (catastrophic backtracking); use a simpler one"
        ) from exc
