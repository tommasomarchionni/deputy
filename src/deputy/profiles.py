"""Agent profiles: markdown files with a small front matter. Project > user > bundled."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from deputy.config import config_dir

TURN_LIMITS = (1, 50)
RETRY_LIMITS = (0, 5)


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    body: str
    source: str
    max_turns: int | None = None  # None: the measured or default limit applies
    max_retries: int | None = None  # None: the default (one correction round) applies
    problems: tuple[str, ...] = ()  # what was wrong in the front matter (those keys are ignored)


def _limit(meta: dict[str, str], key: str, bounds: tuple[int, int]) -> tuple[int | None, str]:
    """A whole number within `bounds` from the front matter, or (None, why it was ignored)."""
    raw = meta.get(key)
    if raw is None:
        return None, ""
    low, high = bounds
    try:
        value = int(raw)
    except ValueError:
        return None, f"{key}: '{raw}' is not a whole number (ignored)"
    if not low <= value <= high:
        return None, f"{key}: {value} is outside {low}-{high} (ignored)"
    return value, ""


def parse_profile(text: str, source: str, fallback_name: str) -> Profile:
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---\n"):
        head, _, rest = text[4:].partition("\n---\n")
        for line in head.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                meta[key.strip()] = value.strip()
        body = rest.lstrip("\n")
    turns, turns_problem = _limit(meta, "max_turns", TURN_LIMITS)
    retries, retries_problem = _limit(meta, "max_retries", RETRY_LIMITS)
    return Profile(
        name=meta.get("name", fallback_name),
        description=meta.get("description", ""),
        body=body.strip(),
        source=source,
        max_turns=turns,
        max_retries=retries,
        problems=tuple(p for p in (turns_problem, retries_problem) if p),
    )


def _from_dir(directory: Path, label: str) -> dict[str, Profile]:
    found: dict[str, Profile] = {}
    if directory.is_dir():
        for file in sorted(directory.glob("*.md")):
            profile = parse_profile(file.read_text(encoding="utf-8"), label, file.stem)
            found[profile.name] = profile
    return found


def load_profiles(project_dir: Path | None = None) -> dict[str, Profile]:
    """Bundled profiles first, then the user's, then the project's (later wins)."""
    found: dict[str, Profile] = {}
    bundled = resources.files("deputy") / "profiles"
    for entry in sorted(bundled.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            profile = parse_profile(entry.read_text(encoding="utf-8"), "bundled", entry.name[:-3])
            found[profile.name] = profile
    found.update(_from_dir(config_dir() / "profiles", "user"))
    if project_dir is not None:
        found.update(_from_dir(project_dir / ".deputy" / "profiles", "project"))
    return found


def bundled_names() -> set[str]:
    folder = resources.files("deputy") / "profiles"
    return {entry.name[:-3] for entry in folder.iterdir() if entry.name.endswith(".md")}


def overrides(profiles: dict[str, Profile]) -> list[str]:
    """A warning for each bundled profile that a user or project copy replaces. Such a copy does
    not get deputy's later improvements to that profile (tools, rules), so say so."""
    return [
        f"profile {name}: your {profiles[name].source} copy replaces the bundled one, so deputy's "
        f"later improvements to it do not reach you; delete the copy (or `deputy optimise "
        f"--revert ID`) to use the current one"
        for name in sorted(bundled_names())
        if name in profiles and profiles[name].source != "bundled"
    ]
