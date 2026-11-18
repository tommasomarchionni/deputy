"""Choose which backend runs a task: the cheapest that is measured to pass, with a fixed fallback.

The order is deterministic, so the same configuration and measurements always give the same
choice, and the reason is recorded in the trace. Rules, in this order:

1. Never route to a more expensive or less private kind than the default backend: with a local
   default, only local backends are candidates; a hosted default may fall back to local ones.
2. Local before hosted.
3. Measured to pass (`pass_rate` at least `POOR_PASS_RATE`) before not measured before measured
   to fail.
4. Faster measured time first.
5. The default backend, then the order of the configuration.

The backends are probed in that order; one that does not answer is skipped, and a run that ends in
a backend error moves on to the next one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from deputy.config import Backend, Config
from deputy.model_profiles import ModelProfile

POOR_PASS_RATE = 0.5
PROBE_TIMEOUT_S = 3.0  # a backend that does not answer this fast is skipped, not waited for

Measured = Callable[[Backend], ModelProfile | None]


@dataclass(frozen=True)
class Candidate:
    backend: Backend
    why: str  # what the ranking saw, for the trace


def _tier(profile: ModelProfile | None) -> int:
    if profile is None:
        return 1
    return 0 if profile.pass_rate >= POOR_PASS_RATE else 2


def _why(backend: Backend, profile: ModelProfile | None) -> str:
    if profile is None:
        return f"{backend.name} ({backend.kind}, not measured)"
    seen = f"{profile.pass_rate:.0%} passed, {profile.mean_elapsed_s:.0f}s"
    return f"{backend.name} ({backend.kind}, {seen})"


def candidates(config: Config, measured: Measured) -> list[Candidate]:
    """The backends allowed for a task, best first."""
    default = config.backend()
    allowed = [b for b in config.backends if default.kind == "hosted" or b.kind == "local"]
    position = {b.name: i for i, b in enumerate(config.backends)}

    def key(backend: Backend) -> tuple[int, int, float, int, int]:
        profile = measured(backend)
        return (
            0 if backend.kind == "local" else 1,
            _tier(profile),
            profile.mean_elapsed_s if profile else 0.0,
            0 if backend.name == default.name else 1,
            position[backend.name],
        )

    return [Candidate(b, _why(b, measured(b))) for b in sorted(allowed, key=key)]
