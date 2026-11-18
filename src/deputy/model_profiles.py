"""The measured profile of a model on a server: what `deputy bench` learned, read back for use."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

from deputy.config import Backend, state_dir

DEFAULT_MAX_TURNS = 8
MIN_MAX_TURNS = 4
MAX_MAX_TURNS = 12
TURN_HEADROOM = 2.0  # the limit is the measured mean number of turns times this
SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class ModelProfile:
    backend: str
    model: str  # the name in the configuration
    served_model: str  # the name the server answered with when it was measured ("" if unknown)
    measured_at: float
    pass_rate: float
    mean_turns: float
    mean_elapsed_s: float


def profile_path(backend: Backend) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{backend.name}__{backend.model}")
    return state_dir() / "models" / f"{safe}.json"


def load_profile(backend: Backend) -> ModelProfile | None:
    """The measured profile of this backend, or None if it was never measured or is unreadable."""
    path = profile_path(backend)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        baseline = data["baseline"]
        return ModelProfile(
            backend=str(data["backend"]),
            model=str(data["model"]),
            served_model=str(data.get("served_model") or ""),
            measured_at=float(data.get("measured_at") or path.stat().st_mtime),
            pass_rate=float(baseline["pass_rate"]),
            mean_turns=float(baseline["mean_turns"]),
            mean_elapsed_s=float(baseline["mean_elapsed_s"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def suggested_max_turns(profile: ModelProfile | None) -> int:
    """A turn limit that fits the model: its measured mean turns with headroom, within bounds."""
    if profile is None or profile.mean_turns <= 0:
        return DEFAULT_MAX_TURNS
    wanted = math.ceil(profile.mean_turns * TURN_HEADROOM)
    return max(MIN_MAX_TURNS, min(MAX_MAX_TURNS, wanted))


def changed_model(profile: ModelProfile | None, served: list[str]) -> str | None:
    """The model the server serves now, when it is not the one that was measured."""
    if profile and profile.served_model and served and profile.served_model not in served:
        return served[0]
    return None


def describe(profile: ModelProfile | None, served: list[str], *, now: float | None = None) -> str:
    """One sentence for `status`: how old the measurement is, and whether the model changed."""
    if profile is None:
        return "never measured: run deputy tune"
    age = ((now if now is not None else time.time()) - profile.measured_at) / SECONDS_PER_DAY
    when = "today" if age < 1 else f"{age:.0f} days ago"
    summary = f"measured {when}: {profile.pass_rate:.0%} passed, {profile.mean_turns:.1f} turns"
    if (moved := changed_model(profile, served)) is not None:
        now_serving = f"the server now serves {moved}, not {profile.served_model}"
        return f"{summary}; {now_serving}: run deputy tune"
    return summary
