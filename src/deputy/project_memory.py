"""What deputy remembers about one repository: where its rules live and what its words mean.

Memory here is **pointers first**: it says which file to read and why, instead of copying the
file, so it cannot go stale or contradict the repository. It is stored outside the repository
(in the state directory), keyed by the repository it describes, and is only ever added to the
prompt of a task whose working directory is inside that repository. Workers cannot write it: it
is changed only with `deputy memory`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, Field

from deputy.config import state_dir
from deputy.sandbox import is_secret_name

MAX_POINTERS = 12
MAX_TERMS = 20
MAX_WHY_CHARS = 120
MAX_TERM_CHARS = 40
MAX_MEANING_CHARS = 200
PROMPT_CHARS = 1_500  # what is added to a worker prompt, at most
CANDIDATE_FILES = ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", ".github/copilot-instructions.md")
CANDIDATE_FOLDERS = (".github/instructions", ".claude/rules")


class ProjectMemoryError(ValueError):
    """A request the memory refuses."""


class Pointer(BaseModel):
    path: str  # relative to the repository root
    why: str


class Term(BaseModel):
    term: str
    meaning: str


class ProjectMemory(BaseModel):
    repo: str = ""  # the root this memory describes (kept to make an export self-explaining)
    pointers: list[Pointer] = Field(default_factory=list[Pointer])
    terms: list[Term] = Field(default_factory=list[Term])


def repo_root(workdir: Path) -> Path:
    """The repository a working directory belongs to: the nearest folder with a `.git`, or the
    working directory itself when it is not in a repository."""
    start = workdir.resolve()
    for folder in (start, *start.parents):
        if (folder / ".git").exists():
            return folder
    return start


def repo_id(root: Path) -> str:
    """A stable key for a repository: the hash of its real path, so two checkouts never share."""
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]


def _file(root: Path) -> Path:
    return state_dir() / "projects" / repo_id(root) / "memory.json"


def load(root: Path) -> ProjectMemory:
    """The memory of this repository; a missing or damaged file is an empty memory."""
    try:
        return ProjectMemory.model_validate_json(_file(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ProjectMemory(repo=str(root.resolve()))


def save(root: Path, memory: ProjectMemory) -> None:
    path = _file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        memory.model_copy(update={"repo": str(root.resolve())}).model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def add_pointer(memory: ProjectMemory, root: Path, path: str, why: str) -> ProjectMemory:
    """Point at a file of the repository. The file must exist inside it and not look secret."""
    target = (root / path).resolve()
    if root.resolve() not in target.parents or not target.is_file():
        raise ProjectMemoryError(f"{path} is not a file inside the repository")
    relative = target.relative_to(root.resolve()).as_posix()
    if is_secret_name(relative):
        raise ProjectMemoryError(f"{relative} looks like a secret and is never remembered")
    if not why.strip() or len(why) > MAX_WHY_CHARS:
        raise ProjectMemoryError(f"say why in 1 to {MAX_WHY_CHARS} characters")
    kept = [p for p in memory.pointers if p.path != relative]
    if len(kept) >= MAX_POINTERS:
        raise ProjectMemoryError(f"at most {MAX_POINTERS} pointers; remove one first")
    return memory.model_copy(update={"pointers": [*kept, Pointer(path=relative, why=why.strip())]})


def add_term(memory: ProjectMemory, term: str, meaning: str) -> ProjectMemory:
    if not term.strip() or len(term) > MAX_TERM_CHARS:
        raise ProjectMemoryError(f"a term is 1 to {MAX_TERM_CHARS} characters")
    if not meaning.strip() or len(meaning) > MAX_MEANING_CHARS:
        raise ProjectMemoryError(f"a meaning is 1 to {MAX_MEANING_CHARS} characters")
    kept = [t for t in memory.terms if t.term.lower() != term.strip().lower()]
    if len(kept) >= MAX_TERMS:
        raise ProjectMemoryError(f"at most {MAX_TERMS} terms; remove one first")
    return memory.model_copy(
        update={"terms": [*kept, Term(term=term.strip(), meaning=meaning.strip())]}
    )


def remove(memory: ProjectMemory, key: str) -> ProjectMemory:
    """Forget the pointer with this path, or the term with this name."""
    pointers = [p for p in memory.pointers if p.path != key]
    terms = [t for t in memory.terms if t.term.lower() != key.lower()]
    if len(pointers) == len(memory.pointers) and len(terms) == len(memory.terms):
        raise ProjectMemoryError(f"nothing remembered under '{key}'")
    return memory.model_copy(update={"pointers": pointers, "terms": terms})


def candidates(root: Path) -> list[Pointer]:
    """Rule files that exist in this repository and are worth a pointer."""
    found: list[Pointer] = []
    for name in CANDIDATE_FILES:
        if (root / name).is_file():
            found.append(
                Pointer(path=name, why="the rules of this repository: read before changing it")
            )
    for folder in CANDIDATE_FOLDERS:
        for file in sorted((root / folder).glob("*.md")) if (root / folder).is_dir() else []:
            found.append(Pointer(path=file.relative_to(root).as_posix(), why="rules for one area"))
    return found[:MAX_POINTERS]


def prompt_notes(memory: ProjectMemory) -> str:
    """The text added to a worker's prompt: pointers and terms, cut to a fixed size."""
    if not memory.pointers and not memory.terms:
        return ""
    lines = ["\n\nNotes about the repository you work in (read the files they point to):"]
    lines += [f"- {p.path}: {p.why}" for p in memory.pointers]
    if memory.terms:
        lines.append("Terms:")
        lines += [f"- {t.term}: {t.meaning}" for t in memory.terms]
    return "\n".join(lines)[:PROMPT_CHARS]


def to_markdown(memory: ProjectMemory) -> str:
    """A reviewable, diffable view of the memory."""
    out = [f"# deputy memory for {memory.repo or 'this repository'}", ""]
    out += ["## Where the rules live", ""]
    out += [f"- `{p.path}`: {p.why}" for p in memory.pointers] or ["- (nothing yet)"]
    out += ["", "## Terms", ""]
    out += [f"- **{t.term}**: {t.meaning}" for t in memory.terms] or ["- (nothing yet)"]
    return "\n".join(out) + "\n"
