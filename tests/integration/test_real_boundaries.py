# pyright: basic
"""Integration: real HTTP, a real subprocess and the real MCP protocol; only the model is faked."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from fake_model import FakeModel
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from deputy import lessons, runs
from deputy.agent import run_task
from deputy.backends import ChatClient
from deputy.bench import Summary
from deputy.checks import CitesLines, Contains
from deputy.config import Backend, Policy, load_config, state_dir
from deputy.lessons import Evidence, Lesson, LessonBook
from deputy.profiles import load_profiles
from deputy.sandbox import Sandbox
from deputy.tune import save_model_profile
from helpers import message, tool_call


def test_the_client_crosses_a_real_socket_with_the_key_and_the_tools(
    model: FakeModel, tmp_path: Path
) -> None:
    key = tmp_path / "key"
    key.write_text("s3cret")
    key.chmod(0o600)
    model.required_key = "s3cret"
    model.queue(message("hello"))
    backend = Backend(name="b", base_url=model.base_url, api_key_file=str(key))

    async def go() -> str:
        client = ChatClient(backend)
        try:
            ok, detail = await client.health()
            assert ok, detail
            return (
                await client.chat([{"role": "user", "content": "hi"}], [{"type": "function"}])
            ).content
        finally:
            await client.aclose()

    assert asyncio.run(go()) == "hello"
    assert model.requests[0]["tools"] == [{"type": "function"}]
    assert model.headers[0]["authorization"] == "Bearer s3cret"


def test_a_wrong_key_is_reported_by_health(model: FakeModel) -> None:
    model.required_key = "right"

    async def go() -> tuple[bool, str]:
        client = ChatClient(Backend(name="b", base_url=model.base_url, api_key_env="NOPE"))
        try:
            return await client.health()
        finally:
            await client.aclose()

    assert asyncio.run(go()) == (False, "reachable, but the API key was refused")


def test_an_agent_run_over_real_http_reads_a_file_and_passes_its_checks(
    model: FakeModel, tmp_path: Path
) -> None:
    (tmp_path / "a.txt").write_text("alpha\nbeta\ngamma\n")
    model.queue(
        message(tool_calls=[tool_call("read_file", {"path": "a.txt"})]),
        message("beta is on line 2"),
    )

    async def go():
        client = ChatClient(Backend(name="b", base_url=model.base_url))
        try:
            return await run_task(
                client=client,
                profile=load_profiles()["helper"],
                task="where is beta?",
                sandbox=Sandbox(tmp_path),
                checks=[CitesLines(max=3), Contains(values=["beta"])],
                policy=Policy(),
            )
        finally:
            await client.aclose()

    result = asyncio.run(go())
    assert result.status == "ok"
    second_request = model.requests[1]["messages"]
    assert any(m["role"] == "tool" and "2: beta" in m["content"] for m in second_request)


def test_the_doctor_command_in_a_subprocess_reports_a_healthy_backend(
    configured: FakeModel,
) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "deputy", "doctor"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "backend fake: ok" in done.stdout


def test_the_mcp_server_speaks_the_protocol_end_to_end(
    configured: FakeModel, tmp_path: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "notes.md").write_text("one\ntwo\nthree\n")
    configured.queue(
        message(tool_calls=[tool_call("read_file", {"path": "notes.md"})]),
        message("The second line is two (line 2)."),
    )

    async def go() -> dict:
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "deputy", "mcp"], env=dict(os.environ)
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            names = sorted(t.name for t in (await session.list_tools()).tools)
            assert names == ["collect", "delegate", "digest", "feedback", "status"]
            status = await session.call_tool("status", {})
            assert status.structured_content["fake"].startswith("up")
            reply = await session.call_tool(
                "delegate",
                {
                    "task": "what is on the second line?",
                    "workdir": str(work),
                    "checks": [
                        {"type": "cites_lines", "max": 3},
                        {"type": "contains", "values": ["two"]},
                    ],
                },
            )
            data = reply.structured_content
            await session.call_tool("feedback", {"run_id": data["run_id"], "verdict": "accepted"})
            return data

    data = asyncio.run(go())
    assert data["status"] == "ok"
    assert data["checks"] == "2/2 passed"
    events = [json.loads(line) for line in (state_dir() / "runs.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events] == ["run", "feedback"]
    assert load_config().backend().name == "fake"


def test_digest_proves_its_citations_over_the_real_protocol(
    configured: FakeModel, tmp_path: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "notes.md").write_text("one\nthe second line is here\nthree\n")
    configured.queue(
        message(tool_calls=[tool_call("read_file", {"path": "notes.md"})]),
        message('- it is the second line (notes.md:2 "the second line is here")'),
    )

    async def go() -> dict:
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "deputy", "mcp"], env=dict(os.environ)
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            arguments = {"files": ["notes.md"], "question": "What is here?", "workdir": str(work)}
            reply = await session.call_tool("digest", arguments)
            content = reply.content[0]
            assert isinstance(content, TextContent)
            return json.loads(content.text)

    data = asyncio.run(go())
    assert data["status"] == "ok"
    assert data["checks"] == "2/2 passed"
    assert "notes.md" in configured.requests[0]["messages"][-1]["content"]


def test_the_status_tool_shows_the_usage_line_over_the_real_protocol(configured: FakeModel) -> None:
    runs.record(
        {
            "event": "run",
            "run_id": "r1",
            "profile": "helper",
            "status": "ok",
            "metrics": {},
            "saved_est": 640,
            "read_est": 740,
            "cost_est": 100,
        }
    )

    async def go() -> dict:
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "deputy", "mcp"], env=dict(os.environ)
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return (await session.call_tool("status", {})).structured_content

    status = asyncio.run(go())
    assert status["fake"].startswith("up")
    assert "1 runs" in status["usage"]
    assert "~640 tokens saved" in status["usage"]


def test_the_status_tool_names_the_kinds_of_task_that_do_not_pay_off(configured: FakeModel) -> None:
    now = time.time()
    for index in range(6):
        runs.record(
            {
                "event": "run",
                "run_id": f"r{index}",
                "ts": now,
                "profile": "helper",
                "task_class": "helper:contains",
                "status": "ok",
                "metrics": {},
                "saved_est": 0,
                "read_est": 100,
                "cost_est": 100,
            }
        )

    async def go() -> dict:
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "deputy", "mcp"], env=dict(os.environ)
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return (await session.call_tool("status", {})).structured_content

    status = asyncio.run(go())
    assert "helper:contains: 6 runs, no net token saving" in status["advice"]


def test_the_status_tool_says_whether_the_model_was_measured_and_whether_it_changed(
    configured: FakeModel,
) -> None:
    async def status() -> dict:
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "deputy", "mcp"], env=dict(os.environ)
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return (await session.call_tool("status", {})).structured_content

    assert status_text(asyncio.run(status())) == "never measured: run deputy tune"
    backend = Backend(name="fake", base_url=configured.base_url, model="fake-model")

    def measured(served: str) -> Summary:
        return Summary(variant="baseline", runs=3, pass_rate=1.0, citation_rate=1.0, mean_turns=2.0,
                       mean_tokens_out=1, mean_elapsed_s=1, stdev_elapsed_s=0, tokens_per_s=1,
                       served_model=served)  # fmt: skip

    save_model_profile(backend, measured("fake-model"), [])
    assert "now serves" not in status_text(asyncio.run(status()))
    save_model_profile(backend, measured("another-model"), [])
    changed = status_text(asyncio.run(status()))
    assert "now serves fake-model, not another-model" in changed


def status_text(status: dict) -> str:
    return status["fake profile"]


def test_a_saved_lesson_reaches_the_worker_prompt_over_the_real_protocol(
    configured: FakeModel, tmp_path: Path
) -> None:
    now = time.time()
    lessons.save_book(
        LessonBook(
            lessons=[
                Lesson(
                    id="helper:contains",
                    profile="helper",
                    check_kind="contains",
                    text=lessons.CATALOGUE["contains"],
                    evidence=Evidence(runs=8, failures=4, window_days=30),
                    created_at=now,
                    expires_at=now + 86_400,
                )
            ]
        )
    )
    configured.queue(message("beta is here"))

    async def go() -> dict:
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "deputy", "mcp"], env=dict(os.environ)
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            reply = await session.call_tool(
                "delegate",
                {
                    "task": "say beta",
                    "workdir": str(tmp_path),
                    "checks": [{"type": "contains", "values": ["beta"]}],
                },
            )
            return reply.structured_content

    assert asyncio.run(go())["status"] == "ok"
    system_prompt = configured.requests[0]["messages"][0]["content"]
    assert lessons.CATALOGUE["contains"] in system_prompt
