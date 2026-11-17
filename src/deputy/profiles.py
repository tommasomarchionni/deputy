"""Agent profiles: markdown files with a small front matter. Project > user > bundled."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from deputy.config import config_dir


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    body: str
    source: str


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
    return Profile(
        name=meta.get("name", fallback_name),
        description=meta.get("description", ""),
        body=body.strip(),
        source=source,
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
