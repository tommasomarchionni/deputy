# pyright: basic
import asyncio
import json
from pathlib import Path
from typing import Any

from conftest import message, tool_call
from deputy import cli, server
from deputy.config import state_dir
from deputy.server import build_server

SCHEMA_BUDGET_CHARS = 5_000  # about 1,250 tokens: what the orchestrator carries on every turn


def structured(result: Any) -> dict[str, Any]:
    """The structured payload of a tool call (the SDK types the result as a union)."""
    return result.structured_content


def test_exposes_exactly_four_small_tools() -> None:
    tools = asyncio.run(build_server().list_tools())
    assert sorted(t.name for t in tools) == ["delegate", "feedback", "profiles", "status"]
    size = sum(len(json.dumps(t.model_dump(mode="json"))) for t in tools)
    assert size < SCHEMA_BUDGET_CHARS, f"tool schemas are {size} chars"


def test_feedback_is_recorded_in_the_trace() -> None:
    result = asyncio.run(
        build_server().call_tool("feedback", {"run_id": "r1", "verdict": "accepted"})
    )
    assert result is not None
    line = (state_dir() / "runs.jsonl").read_text().splitlines()[-1]
    assert json.loads(line)["verdict"] == "accepted"


def test_delegate_without_config_returns_a_clear_error(tmp_path: Path) -> None:
    result = asyncio.run(
        build_server().call_tool("delegate", {"task": "x", "workdir": str(tmp_path)})
    )
    text = (
        json.dumps(result.model_dump(mode="json")) if hasattr(result, "model_dump") else str(result)
    )
    assert "deputy init" in text


def test_delegate_runs_end_to_end_with_a_scripted_model(
    tmp_path: Path,
    fake_client,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    (tmp_path / "a.txt").write_text("alpha\nbeta\n")
    script = [
        message(tool_calls=[tool_call("read_file", {"path": "a.txt"})]),
        message("beta, line 2"),
    ]
    monkeypatch.setattr(server, "ChatClient", lambda backend: fake_client(script))
    out = asyncio.run(
        build_server().call_tool(
            "delegate",
            {
                "task": "find beta",
                "workdir": str(tmp_path),
                "checks": [{"type": "cites_lines", "max": 2}],
            },
        )
    )
    data = structured(out)
    assert data["status"] == "ok"
    assert data["answer"] == "beta, line 2"
    assert data["checks"][0]["passed"] is True


def test_profiles_and_status_tools() -> None:
    profiles = asyncio.run(build_server().call_tool("profiles", {}))
    assert "helper" in structured(profiles)
    status = asyncio.run(build_server().call_tool("status", {}))
    assert "deputy init" in structured(status)["error"]


def test_an_invalid_check_is_reported_before_any_work_starts(tmp_path: Path) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    out = asyncio.run(
        build_server().call_tool(
            "delegate",
            {"task": "x", "workdir": str(tmp_path), "checks": [{"type": "cites_lines"}]},
        )
    )
    data = structured(out)
    assert data["status"] == "error"
    assert "invalid check" in data["answer"]
