"""A trace of every run, as JSON lines in the state directory. Never contains a secret."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from deputy.config import state_dir


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _file() -> Path:
    path = state_dir() / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def record(event: dict[str, Any]) -> None:
    """Append one event. A failure to write the trace must never fail the run."""
    try:
        line = json.dumps({"ts": time.time(), **event}, ensure_ascii=False)
        with _file().open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        return
