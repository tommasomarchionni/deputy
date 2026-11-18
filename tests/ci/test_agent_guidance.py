# pyright: basic
"""The guidance files for AI agents must stay consistent: one source of truth per rule."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTRUCTIONS = ROOT / ".github" / "instructions"
RULES = ROOT / ".claude" / "rules"
FILES = sorted(INSTRUCTIONS.glob("*.instructions.md"))


def front_matter(path: Path) -> dict[str, str]:
    head = path.read_text().split("---\n")[1]
    return dict(line.split(": ", 1) for line in head.strip().splitlines())


def test_there_are_per_area_instructions() -> None:
    assert {f.name for f in FILES} >= {
        "python.instructions.md",
        "tests.instructions.md",
        "docs.instructions.md",
        "ci.instructions.md",
    }


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_copilot_and_claude_globs_are_identical(path: Path) -> None:
    meta = front_matter(path)
    assert meta["applyTo"] == meta["paths"], "GitHub Copilot reads applyTo, Claude Code reads paths"
    assert meta["description"]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_glob_matches_something_that_exists(path: Path) -> None:
    for pattern in front_matter(path)["applyTo"].strip("'\"").split(","):
        base = pattern.split("*")[0].rstrip("/")
        assert (ROOT / base).exists(), f"{path.name}: {pattern} points at nothing"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_each_instruction_file_is_linked_from_claude_rules(path: Path) -> None:
    link = RULES / (path.name.removesuffix(".instructions.md") + ".md")
    assert link.is_symlink(), f"{link.name} must be a link to {path.name}"
    assert link.resolve() == path.resolve()


def test_claude_md_imports_agents_md_and_copilot_points_at_it() -> None:
    assert (ROOT / "CLAUDE.md").read_text().startswith("@AGENTS.md")
    assert "AGENTS.md" in (ROOT / ".github" / "copilot-instructions.md").read_text()


def test_agents_md_mentions_every_command_it_tells_agents_to_run() -> None:
    text = (ROOT / "AGENTS.md").read_text()
    for command in (
        "uv sync",
        "uv run ruff check",
        "uv run pyright",
        "uv run pytest",
        "mkdocs build",
    ):
        assert command in text


def test_the_guidance_is_in_english_and_free_of_unfinished_markers() -> None:
    markers = re.compile(r"\b(TODO|FIXME|XXX)\b")
    for path in [*FILES, ROOT / "AGENTS.md", ROOT / "CLAUDE.md"]:
        assert not markers.search(path.read_text()), path.name
