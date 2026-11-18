"""Background delegations: submit now, collect later, cancel when it is no longer wanted.

The jobs live in the memory of the running server. Cancelling stops the worker (the request to the
model server is dropped, a running command check is killed) and puts back every file the worker
wrote, so the workspace is as it was before the job.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

from deputy.agent import Metrics, Result
from deputy.sandbox import Sandbox

MAX_ACTIVE = 3  # one local GPU does not serve many at once
MAX_KEPT = 20  # finished jobs nobody collected are dropped, oldest first
POLL_WAIT_S = 20.0  # `collect` waits this long for a result, so polling costs few tokens


@dataclass
class Job:
    sandbox: Sandbox
    task: asyncio.Task[Result]
    started: float = field(default_factory=time.monotonic)

    def elapsed(self) -> float:
        return round(time.monotonic() - self.started, 1)


def _result(run_id: str, status: str, answer: str, *flags: str, elapsed: float = 0.0) -> Result:
    return Result.model_validate(
        {
            "run_id": run_id,
            "status": status,
            "answer": answer,
            "flags": list(flags),
            "metrics": Metrics(elapsed_s=elapsed),
        }
    )


class Jobs:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def active(self) -> int:
        return sum(not job.task.done() for job in self._jobs.values())

    def has_room(self) -> bool:
        return self.active() < MAX_ACTIVE

    def submit(self, run_id: str, sandbox: Sandbox, work: Coroutine[Any, Any, Result]) -> Result:
        """Start `work` in the background. The caller checked `has_room`."""
        self._jobs[run_id] = Job(sandbox, asyncio.create_task(self._guard(run_id, work)))
        self._forget_old()
        return _result(run_id, "running", f"started; call collect with run_id {run_id}")

    async def collect(self, run_id: str, *, cancel: bool = False) -> Result:
        """The result of a job; with `cancel`, stop it first and undo what it wrote."""
        job = self._jobs.get(run_id)
        if job is None:
            missing = "no such job (finished and collected, or never started)"
            return _result(run_id, "error", missing, "unknown_job")
        if cancel and not job.task.done():
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)
            restored = job.sandbox.rollback()
            del self._jobs[run_id]
            note = f"cancelled; {restored} file(s) put back"
            return _result(run_id, "cancelled", note, "cancelled", elapsed=job.elapsed())
        if not job.task.done():
            await asyncio.wait({job.task}, timeout=POLL_WAIT_S)
        if not job.task.done():
            note = "still running; call collect again"
            return _result(run_id, "running", note, elapsed=job.elapsed())
        del self._jobs[run_id]
        return job.task.result()

    async def cancel_all(self) -> None:
        """Stop every job and undo its files (the server is shutting down)."""
        for run_id in list(self._jobs):
            await self.collect(run_id, cancel=True)

    def _forget_old(self) -> None:
        finished = [rid for rid, job in self._jobs.items() if job.task.done()]
        for run_id in finished[: max(0, len(self._jobs) - MAX_KEPT)]:
            del self._jobs[run_id]

    @staticmethod
    async def _guard(run_id: str, work: Coroutine[Any, Any, Result]) -> Result:
        """An unexpected failure becomes an error result, not a lost job."""
        try:
            return await work
        except Exception as exc:
            return _result(
                run_id, "error", f"internal error: {type(exc).__name__}", "internal_error"
            )
