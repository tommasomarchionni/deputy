"""Disposable working copies: a worker edits a copy, never the real repository."""

from __future__ import annotations

import difflib
import re
import shutil
from pathlib import Path

from deputy.config import state_dir
from deputy.sandbox import is_secret_name

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class WorkspaceError(Exception):
    """A workspace request that cannot be honoured. The message is meant for the user."""


def workspaces_root() -> Path:
    return state_dir() / "workspaces"


def _checked_name(name: str) -> str:
    if not _NAME.fullmatch(name):
        raise WorkspaceError("name must be letters, digits, '.', '_' or '-' (max 64 characters)")
    return name


def work_dir(name: str) -> Path:
    return workspaces_root() / _checked_name(name) / "work"


def _base_dir(name: str) -> Path:
    return workspaces_root() / _checked_name(name) / "base"


def _copy_into(source: Path, repo: Path, target: Path) -> None:
    for file in (
        [source] if source.is_file() else sorted(p for p in source.rglob("*") if p.is_file())
    ):
        relative = file.relative_to(repo)
        if is_secret_name(relative.as_posix()) or ".git" in relative.parts:
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, destination)


def create(name: str, repo: Path, paths: list[str]) -> Path:
    """Copy `paths` (relative to `repo`) into a fresh workspace and return its working directory."""
    repo = repo.resolve()
    if not paths:
        raise WorkspaceError("give at least one file or folder to copy")
    root = workspaces_root() / _checked_name(name)
    if root.exists():
        raise WorkspaceError(f"workspace '{name}' exists; remove it first")
    sources: list[Path] = []
    for raw in paths:
        source = (repo / raw).resolve()
        if repo not in source.parents and source != repo:
            raise WorkspaceError(f"{raw} is outside the repository")
        if not source.exists():
            raise WorkspaceError(f"{raw} does not exist")
        sources.append(source)
    for target in (_base_dir(name), work_dir(name)):
        target.mkdir(parents=True)
        for source in sources:
            _copy_into(source, repo, target)
    return work_dir(name)


def _files(root: Path) -> dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}


def _lines(path: Path | None) -> list[str]:
    if path is None:
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (UnicodeDecodeError, OSError) as exc:
        raise WorkspaceError(f"cannot read {path.name}: not a UTF-8 text file") from exc


def diff(name: str) -> str:
    """A unified diff of everything the worker changed, added or removed. Empty if nothing."""
    base, work = _base_dir(name), work_dir(name)
    if not work.is_dir():
        raise WorkspaceError(f"no workspace named '{name}'")
    before, after = _files(base), _files(work)
    chunks: list[str] = []
    for relative in sorted(before.keys() | after.keys()):
        old, new = _lines(before.get(relative)), _lines(after.get(relative))
        if old != new:
            chunks.extend(
                difflib.unified_diff(old, new, f"a/{relative}", f"b/{relative}", lineterm="\n")
            )
    return "".join(chunks)


def remove(name: str) -> None:
    root = workspaces_root() / _checked_name(name)
    if not root.is_dir():
        raise WorkspaceError(f"no workspace named '{name}'")
    shutil.rmtree(root)
