# pyright: basic
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from deputy import cli, jobs, server
from deputy.agent import Result
from deputy.backends import ChatClient
from deputy.checks import CheckContext, Command
from deputy.config import Backend, Policy
from deputy.jobs import Jobs
from deputy.sandbox import Sandbox
from deputy.server import build_server
from helpers import message, tool_call


def finished(run_id: str) -> Result:
    return Result(run_id=run_id, status="ok", answer="done")


async def never() -> Result:
    await asyncio.sleep(3600)
    raise AssertionError("unreachable")


def test_a_job_is_started_at_once_and_collected_once(tmp_path: Path) -> None:
    async def scenario() -> list[Result]:
        pending = Jobs()
        started = pending.submit("r1", Sandbox(tmp_path), _answer("r1"))
        first = await pending.collect("r1")
        again = await pending.collect("r1")
        return [started, first, again]

    started, first, again = asyncio.run(scenario())
    assert (started.status, started.run_id) == ("running", "r1")
    assert (first.status, first.answer) == ("ok", "done")
    assert (again.status, again.flags) == ("error", ["unknown_job"])


async def _answer(run_id: str) -> Result:
    await asyncio.sleep(0)
    return finished(run_id)


def test_collect_says_still_running_after_its_wait_and_the_job_keeps_going(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "POLL_WAIT_S", 0.05)

    async def scenario() -> tuple[Result, Result]:
        pending = Jobs()
        gate = asyncio.Event()

        async def slow() -> Result:
            await gate.wait()
            return finished("r1")

        pending.submit("r1", Sandbox(tmp_path), slow())
        waiting = await pending.collect("r1")
        gate.set()
        return waiting, await pending.collect("r1")

    waiting, done = asyncio.run(scenario())
    assert waiting.status == "running"
    assert done.status == "ok"


def test_cancelling_stops_the_worker_and_puts_every_file_back(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("original")
    box = Sandbox(tmp_path)

    async def scenario() -> Result:
        pending = Jobs()

        async def writer() -> Result:
            box.write_file("keep.txt", "overwritten")
            box.write_file("new/made.txt", "fresh")
            return await never()

        pending.submit("r1", box, writer())
        await asyncio.sleep(0.01)
        return await pending.collect("r1", cancel=True)

    result = asyncio.run(scenario())
    assert (result.status, result.flags) == ("cancelled", ["cancelled"])
    assert "2 file(s) put back" in result.answer
    assert (tmp_path / "keep.txt").read_text() == "original"
    assert not (tmp_path / "new" / "made.txt").exists()


def test_cancelling_a_finished_job_just_returns_its_result(tmp_path: Path) -> None:
    async def scenario() -> Result:
        pending = Jobs()
        pending.submit("r1", Sandbox(tmp_path), _answer("r1"))
        await asyncio.sleep(0.01)
        return await pending.collect("r1", cancel=True)

    assert asyncio.run(scenario()).status == "ok"


def test_only_a_few_jobs_run_at_once(tmp_path: Path) -> None:
    async def scenario() -> list[bool]:
        pending = Jobs()
        room = [pending.has_room()]
        for n in range(jobs.MAX_ACTIVE):
            pending.submit(f"r{n}", Sandbox(tmp_path), never())
            room.append(pending.has_room())
        await asyncio.sleep(0)  # let the jobs start
        await pending.cancel_all()
        room.append(pending.has_room())
        return room

    assert asyncio.run(scenario()) == [True] * jobs.MAX_ACTIVE + [False, True]


def test_an_unexpected_failure_becomes_an_error_result_not_a_lost_job(tmp_path: Path) -> None:
    async def broken() -> Result:
        raise RuntimeError("secret detail that must not be echoed")

    async def scenario() -> Result:
        pending = Jobs()
        pending.submit("r1", Sandbox(tmp_path), broken())
        return await pending.collect("r1")

    result = asyncio.run(scenario())
    assert (result.status, result.flags) == ("error", ["internal_error"])
    assert "secret detail" not in result.answer


@given(
    writes=st.lists(
        st.tuples(st.sampled_from(["a.txt", "b.txt", "d/c.txt"]), st.text(max_size=40)),
        min_size=1,
        max_size=8,
    ),
    existing=st.dictionaries(st.sampled_from(["a.txt", "b.txt"]), st.text(max_size=40)),
)
def test_rollback_restores_whatever_was_written(
    tmp_path_factory: pytest.TempPathFactory,
    writes: list[tuple[str, str]],
    existing: dict[str, str],
) -> None:
    root = tmp_path_factory.mktemp("rollback")
    for name, text in existing.items():
        (root / name).write_bytes(text.encode())
    box = Sandbox(root)
    for name, text in writes:
        box.write_file(name, text)
    box.rollback()
    found = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")}
    # every file is back as it was, and no folder the worker made is left behind
    assert found == {name: text.encode() for name, text in existing.items()}
    assert box.rollback() == 0  # nothing left to undo
    assert box.files_written == []


def test_cancelling_kills_a_running_command_check(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    argv = [sys.executable, "-c", "import time; time.sleep(60)"]
    ctx = CheckContext(answer="", sandbox=Sandbox(tmp_path), policy=Policy(allowed_commands=[argv]))
    started: list[Any] = []
    real = asyncio.create_subprocess_exec

    async def spy(*args: Any, **kwargs: Any) -> Any:
        process = await real(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)

    async def scenario() -> int | None:
        task = asyncio.create_task(Command(argv=argv).evaluate(ctx))
        while not started:
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return await asyncio.wait_for(started[0].wait(), timeout=5)

    assert asyncio.run(scenario()) != 0  # killed, not finished by itself


class GatedModel:
    """A scripted model server whose second answer waits for the test to open the gate."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self.script = iter(script)
        self.gate = asyncio.Event()
        self.calls = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls == 2:
            await self.gate.wait()
        return httpx.Response(200, json=next(self.script))

    def client(self) -> ChatClient:
        backend = Backend(name="fake", base_url="http://fake", model="fake-model")
        return ChatClient(backend, httpx.AsyncClient(transport=httpx.MockTransport(self.handler)))


def payload(result: Any) -> dict[str, Any]:
    if result.structured_content:
        return result.structured_content
    return json.loads(result.content[0].text)


def test_a_background_delegation_runs_and_is_collected_with_the_same_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    (tmp_path / "a.txt").write_text("alpha\nbeta\n")
    model = GatedModel(
        [message(tool_calls=[tool_call("read_file", {"path": "a.txt"})]), message("beta, line 2")]
    )
    monkeypatch.setattr(server, "ChatClient", lambda backend: model.client())

    async def scenario() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        mcp = build_server()
        args = {"task": "find beta", "workdir": str(tmp_path), "wait": False}
        started = payload(await mcp.call_tool("delegate", args))
        monkeypatch.setattr(jobs, "POLL_WAIT_S", 0.05)
        waiting = payload(await mcp.call_tool("collect", {"run_id": started["run_id"]}))
        model.gate.set()
        monkeypatch.setattr(jobs, "POLL_WAIT_S", 20.0)
        done = payload(await mcp.call_tool("collect", {"run_id": started["run_id"]}))
        return started, waiting, done

    started, waiting, done = asyncio.run(scenario())
    assert started["status"] == "running"
    assert waiting["status"] == "running"
    assert done["status"] == "ok"
    assert done["answer"] == "beta, line 2"
    assert done["run_id"] == started["run_id"]  # one id from start to feedback


def test_cancelling_a_background_delegation_leaves_the_workspace_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    model = GatedModel(
        [
            message(tool_calls=[tool_call("write_file", {"path": "out.txt", "content": "half"})]),
            message("never used"),
        ]
    )
    monkeypatch.setattr(server, "ChatClient", lambda backend: model.client())

    async def scenario() -> tuple[dict[str, Any], bool]:
        mcp = build_server()
        args = {"task": "write", "workdir": str(tmp_path), "wait": False}
        started = payload(await mcp.call_tool("delegate", args))
        while not (tmp_path / "out.txt").exists():  # the worker has written its first file
            await asyncio.sleep(0.01)
        cancelled = payload(
            await mcp.call_tool("collect", {"run_id": started["run_id"], "cancel": True})
        )
        return cancelled, (tmp_path / "out.txt").exists()

    cancelled, left_over = asyncio.run(scenario())
    assert cancelled["status"] == "cancelled"
    assert left_over is False
