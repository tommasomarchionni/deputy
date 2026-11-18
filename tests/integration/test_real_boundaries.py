# pyright: basic
"""Integration: real HTTP, a real subprocess and the real MCP protocol; only the model is faked."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from fake_model import FakeModel
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from deputy import runs
from deputy.agent import run_task
from deputy.backends import ChatClient
from deputy.checks import CitesLines, Contains
from deputy.config import Backend, Policy, load_config, state_dir
from deputy.profiles import load_profiles
from deputy.sandbox import Sandbox
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
            assert names == ["delegate", "feedback", "profiles", "status"]
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
    assert [c["passed"] for c in data["checks"]] == [True, True]
    events = [json.loads(line) for line in (state_dir() / "runs.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events] == ["run", "feedback"]
    assert load_config().backend().name == "fake"


def test_the_status_tool_shows_the_usage_line_over_the_real_protocol(configured: FakeModel) -> None:
    runs.record(
        {
            "event": "run",
            "run_id": "r1",
            "profile": "helper",
            "status": "ok",
            "metrics": {},
            "saved_est": 640,
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
