# pyright: basic
"""A thin wrapper over the `gh` CLI, the only way these scripts talk to GitHub."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any


def gh(*args: str, payload: dict[str, Any] | None = None) -> Any:
    """Run `gh api <args>` against GH_REPO paths and return the decoded JSON ('' if empty)."""
    command = ["gh", "api", *args]
    if payload is not None:
        command += ["--input", "-"]
    done = subprocess.run(  # noqa: S603
        command,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout) if done.stdout.strip() else ""


def repo() -> str:
    return os.environ["GH_REPO"]
