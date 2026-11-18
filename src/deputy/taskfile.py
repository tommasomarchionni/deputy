"""`deputy bench --tasks-file`: measure delegation on your own folder with your own tasks.

A task file is TOML: one `[[task]]` table per task with a `name`, the `task` text and optional
`checks` (the same fields as the `delegate` tool). Every task runs in the folder you name; whatever
the worker writes is rolled back after the task, so the folder is left as it was.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from deputy.agent import Result, run_task
from deputy.backends import ChatClient
from deputy.checks import Check, CheckSpec, to_check
from deputy.config import Backend, ConfigError, Policy
from deputy.profiles import Profile, load_profiles
from deputy.runs import BENCH_SOURCE
from deputy.sandbox import Sandbox

ClientFactory = Callable[[Backend], ChatClient]


@dataclass(frozen=True)
class FileTask:
    name: str
    task: str
    checks: list[Check] = field(default_factory=list[Check])


@dataclass(frozen=True)
class Row:
    name: str
    passed: bool
    status: str
    turns: int
    seconds: float
    saved_tokens: int


def load_tasks(path: Path) -> list[FileTask]:
    """Read and validate a task file. Raises ConfigError with a message fit for the user."""
    try:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    raw: list[dict[str, Any]] = data.get("task", [])
    if not raw:
        raise ConfigError(f"{path} has no [[task]] table")
    out: list[FileTask] = []
    for index, entry in enumerate(raw, start=1):
        name, text = entry.get("name"), entry.get("task")
        if not isinstance(name, str) or not isinstance(text, str) or not name or not text:
            raise ConfigError(f"task {index} needs a `name` and a `task` text")
        try:
            checks = [to_check(CheckSpec(**spec)) for spec in entry.get("checks", [])]
        except (ValidationError, TypeError) as exc:
            raise ConfigError(f"task '{name}' has an invalid check: {exc}") from exc
        out.append(FileTask(name, text, checks))
    return out


async def run_tasks(  # noqa: PLR0913
    tasks: list[FileTask],
    workdir: Path,
    backend: Backend,
    *,
    policy: Policy,
    profile: Profile | None = None,
    client_factory: ClientFactory = ChatClient,
    on_row: Callable[[Row], None] | None = None,
) -> list[Row]:
    chosen = profile or load_profiles(workdir)["helper"]
    rows: list[Row] = []
    for task in tasks:
        sandbox = Sandbox(workdir)
        client = client_factory(backend)
        try:
            result = await run_task(
                client=client,
                profile=chosen,
                task=task.task,
                sandbox=sandbox,
                checks=task.checks,
                policy=policy,
                max_retries=0,
                source=BENCH_SOURCE,
            )
        finally:
            await client.aclose()
            sandbox.rollback()
        rows.append(_row(task.name, result))
        if on_row:
            on_row(rows[-1])
    return rows


def _row(name: str, result: Result) -> Row:
    return Row(
        name=name,
        passed=result.status == "ok",
        status=result.status,
        turns=result.metrics.turns,
        seconds=result.metrics.elapsed_s,
        saved_tokens=result.orchestrator_tokens_saved_est,
    )


def format_rows(rows: list[Row]) -> str:
    width = max(8, *(len(r.name) for r in rows)) + 2
    head = f"{'task':<{width}}{'result':<16}{'turns':>6}{'sec':>8}{'saved*':>9}"
    lines = [head]
    lines += [
        f"{r.name:<{width}}{r.status:<16}{r.turns:>6}{r.seconds:>8.1f}{r.saved_tokens:>9}"
        for r in rows
    ]
    passed = sum(r.passed for r in rows)
    lines.append(
        f"{'total':<{width}}{f'{passed}/{len(rows)} ok':<16}{sum(r.turns for r in rows):>6}"
        f"{sum(r.seconds for r in rows):>8.1f}{sum(r.saved_tokens for r in rows):>9}"
    )
    lines.append("* estimated tokens the orchestrator did not have to read (can be negative)")
    return "\n".join(lines)
