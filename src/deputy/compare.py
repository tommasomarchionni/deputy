"""`deputy bench --compare A.json B.json`: what changed between two benchmark reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from deputy.bench import Summary
from deputy.config import ConfigError

# A pass rate that fell by more than this (in points of 1.0) counts as a regression.
PASS_RATE_DROP = 0.05


def load_baseline(path: Path) -> Summary:
    """The baseline summary of a report written by `deputy bench --json`."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Summary.model_validate(data["summaries"][0])
    except (OSError, ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
        raise ConfigError(f"{path} is not a `deputy bench --json` report: {exc}") from exc


@dataclass(frozen=True)
class Comparison:
    text: str
    regressed: bool


def _delta(before: float, after: float, unit: str = "", digits: int = 1) -> str:
    return f"{before:.{digits}f}{unit} -> {after:.{digits}f}{unit} ({after - before:+.{digits}f})"


def compare(before: Summary, after: Summary) -> Comparison:
    worse = [
        task
        for task, count in sorted(after.failures.items())
        if count > before.failures.get(task, 0)
    ]
    better = [
        task
        for task, count in sorted(before.failures.items())
        if count > after.failures.get(task, 0)
    ]
    regressed = bool(worse) or before.pass_rate - after.pass_rate > PASS_RATE_DROP
    lines = [
        f"pass rate     {_delta(before.pass_rate * 100, after.pass_rate * 100, '%', 0)}",
        f"time per task {_delta(before.mean_elapsed_s, after.mean_elapsed_s, ' s')}",
        f"saved (est.)  {_delta(before.mean_saved_tokens_est, after.mean_saved_tokens_est, '', 0)}",
    ]
    if before.served_model != after.served_model:
        lines.append(f"model changed: {before.served_model or '?'} -> {after.served_model or '?'}")
    lines += [f"failed more: {task}" for task in worse]
    lines += [f"failed less: {task}" for task in better]
    lines.append("REGRESSION" if regressed else "no regression")
    return Comparison("\n".join(lines), regressed)
