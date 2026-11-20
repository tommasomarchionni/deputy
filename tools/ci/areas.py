# pyright: basic
"""The areas of the repository: one place that maps files to area labels and PR title scopes."""

from __future__ import annotations

# (path prefix, area). The first matching prefix wins; a file can match only one area.
PATH_AREAS: tuple[tuple[str, str], ...] = (
    ("src/deputy/sandbox.py", "sandbox"),
    ("src/deputy/checks.py", "checks"),
    ("src/deputy/bench.py", "bench"),
    ("src/deputy/tune.py", "bench"),
    ("src/deputy/cli/", "cli"),
    ("src/deputy/install.py", "cli"),
    ("src/deputy/updates.py", "cli"),
    ("src/deputy/version.py", "cli"),
    ("src/deputy/", "server"),
    ("install.sh", "installer"),
    ("pyproject.toml", "installer"),
    ("docs/", "docs"),
    ("mkdocs.yml", "docs"),
    ("README.md", "docs"),
    ("CONTRIBUTING.md", "docs"),
    (".github/", "ci"),
    ("tools/ci/", "ci"),
)

SCOPES: frozenset[str] = frozenset({area for _, area in PATH_AREAS} | {"memory", "router", "deps"})


def area_of(path: str) -> str | None:
    for prefix, area in PATH_AREAS:
        if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
            return area
    return None


def areas_of(paths: list[str]) -> list[str]:
    """The distinct areas touched, in a stable order."""
    return sorted({area for path in paths if (area := area_of(path))})


def is_product_path(path: str) -> bool:
    """Files that ship to users: a `feat` or `fix` must change one of these."""
    return path.startswith("src/deputy/") or path == "install.sh"


def is_test_path(path: str) -> bool:
    return path.startswith("tests/")
