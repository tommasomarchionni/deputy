"""Agent profiles: markdown files with a small front matter. Project > user > bundled.

A profile can say `extends: helper`: it then gets the current text of that profile followed by its
own lines, so what deputy improves in the parent is never left behind in a copy.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    extends: str = ""  # the profile whose current text comes before this one's


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
    for key in ("max_turns", "max_retries", "extends"):  # these may carry a trailing comment
        if key in meta:
            meta[key] = meta[key].split(" #", 1)[0].strip()
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
        extends=meta.get("extends", ""),
    )


def _from_dir(directory: Path, label: str) -> dict[str, Profile]:
    found: dict[str, Profile] = {}
    if directory.is_dir():
        for file in sorted(directory.glob("*.md")):
            profile = parse_profile(file.read_text(encoding="utf-8"), label, file.stem)
            found[profile.name] = profile
    return found


Layers = list[dict[str, Profile]]


def _parent(profile: Profile, level: int, layers: Layers) -> tuple[int, Profile] | None:
    """Where `extends` points: the winning profile of that name, or, for a profile that extends
    its own name, the one in the layers below it."""
    top = level if profile.extends == profile.name else len(layers)
    for index in range(top - 1, -1, -1):
        if profile.extends in layers[index]:
            return index, layers[index][profile.extends]
    return None


def _resolved(
    profile: Profile,
    level: int,
    layers: Layers,
    seen: frozenset[tuple[int, str]] = frozenset(),
) -> Profile:
    """The profile with its parent's current text before its own; a bad `extends` is reported."""
    if not profile.extends:
        return profile
    here = seen | {(level, profile.name)}
    found = _parent(profile, level, layers)
    if found is None:
        problem = f"extends: no profile named '{profile.extends}' (ignored)"
        return replace(profile, problems=(*profile.problems, problem))
    index, parent = found
    if (index, parent.name) in here:
        problem = f"extends: '{profile.extends}' leads back to this profile (ignored)"
        return replace(profile, problems=(*profile.problems, problem))
    base = _resolved(parent, index, layers, here)
    return replace(
        profile,
        body=f"{base.body}\n\n{profile.body}".strip(),
        description=profile.description or base.description,
        max_turns=base.max_turns if profile.max_turns is None else profile.max_turns,
        max_retries=base.max_retries if profile.max_retries is None else profile.max_retries,
        problems=(*profile.problems, *(p for p in base.problems if p.startswith("extends:"))),
    )


def load_profiles(project_dir: Path | None = None) -> dict[str, Profile]:
    """Bundled profiles first, then the user's, then the project's (later wins)."""
    bundled: dict[str, Profile] = {}
    folder = resources.files("deputy") / "profiles"
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            profile = parse_profile(entry.read_text(encoding="utf-8"), "bundled", entry.name[:-3])
            bundled[profile.name] = profile
    layers = [bundled, _from_dir(config_dir() / "profiles", "user")]
    if project_dir is not None:
        layers.append(_from_dir(project_dir / ".deputy" / "profiles", "project"))
    found: dict[str, Profile] = {}
    for level, layer in enumerate(layers):
        found.update({name: _resolved(p, level, layers) for name, p in layer.items()})
    return found


def bundled_names() -> set[str]:
    folder = resources.files("deputy") / "profiles"
    return {entry.name[:-3] for entry in folder.iterdir() if entry.name.endswith(".md")}


def overrides(profiles: dict[str, Profile]) -> list[str]:
    """A warning for each bundled profile that a user or project copy replaces. Such a copy does
    not get deputy's later improvements to that profile (tools, rules), so say so."""
    return [
        f"profile {name}: your {profiles[name].source} copy replaces the bundled one, so deputy's "
        f"later improvements to it do not reach you; delete the copy, or keep only your own lines "
        f"and add `extends: {name}`"
        for name in sorted(bundled_names())
        if name in profiles
        and profiles[name].source != "bundled"
        and not _builds_on_itself(profiles[name])
    ]


def _builds_on_itself(profile: Profile) -> bool:
    """A profile that extends the bundled one of its own name stays current."""
    return profile.extends == profile.name and not any(
        p.startswith("extends:") for p in profile.problems
    )
