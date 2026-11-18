# pyright: basic
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from deputy import cli, model_profiles, server
from deputy.backends import Probe
from deputy.bench import Summary
from deputy.config import Backend, state_dir
from deputy.server import build_server
from deputy.tune import save_model_profile
from helpers import message, tool_call

SCHEMA_BUDGET_CHARS = 5_000  # about 1,250 tokens: what the orchestrator carries on every turn


def structured(result: Any) -> dict[str, Any]:
    """The structured payload of a tool call (the SDK types the result as a union)."""
    return result.structured_content


def test_exposes_exactly_five_small_tools() -> None:
    tools = asyncio.run(build_server().list_tools())
    assert sorted(t.name for t in tools) == [
        "collect",
        "delegate",
        "feedback",
        "profiles",
        "status",
    ]
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
    assert data["checks"] == "1/1 passed"
    assert "metrics" not in data  # they stay in the trace, not in what the orchestrator reads


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


def _delegate_with_history(
    tmp_path: Path,
    fake_client,
    monkeypatch,
    history_saved: int,  # type: ignore[no-untyped-def]
) -> dict[str, Any]:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    now = time.time()
    path = state_dir() / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    past = [
        {
            "event": "run", "run_id": f"h{i}", "ts": now, "profile": "helper",
            "task_class": "helper:contains", "status": "ok", "flags": [], "metrics": {},
            "saved_est": history_saved,
        }
        for i in range(6)
    ]  # fmt: skip
    path.write_text("\n".join(json.dumps(line) for line in past) + "\n")
    monkeypatch.setattr(server, "ChatClient", lambda backend: fake_client([message("beta")]))
    out = asyncio.run(
        build_server().call_tool(
            "delegate",
            {
                "task": "find beta",
                "workdir": str(tmp_path),
                "checks": [{"type": "contains", "values": ["beta"]}],
            },
        )
    )
    return structured(out)


def test_a_delegation_is_flagged_when_that_kind_of_task_has_not_paid_off(
    tmp_path: Path,
    fake_client,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    data = _delegate_with_history(tmp_path, fake_client, monkeypatch, history_saved=0)
    assert data["status"] == "ok"
    assert "not_worth_delegating" in data["flags"]


def test_a_delegation_is_not_flagged_when_that_kind_of_task_pays_off(
    tmp_path: Path,
    fake_client,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    data = _delegate_with_history(tmp_path, fake_client, monkeypatch, history_saved=900)
    assert "not_worth_delegating" not in (data.get("flags") or [])


def _looping_model(fake_client, monkeypatch, steps: int) -> None:  # type: ignore[no-untyped-def]
    loop = [message(tool_calls=[tool_call("grep", {"pattern": "x"})]) for _ in range(steps)]
    monkeypatch.setattr(server, "ChatClient", lambda backend: fake_client(list(loop)))


def _last_metrics() -> dict[str, Any]:
    """The metrics of the last run, from the trace (a result no longer carries them)."""
    lines = (state_dir() / "runs.jsonl").read_text().splitlines()
    return [json.loads(x) for x in lines if '"event": "run"' in x][-1]["metrics"]


def _turns_used(tmp_path: Path, **arguments: Any) -> int:
    asyncio.run(
        build_server().call_tool(
            "delegate", {"task": "loop forever", "workdir": str(tmp_path), **arguments}
        )
    )
    return _last_metrics()["turns"]


def test_the_turn_limit_comes_from_the_measured_profile_unless_given(
    tmp_path: Path,
    fake_client,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    _looping_model(fake_client, monkeypatch, steps=20)
    assert _turns_used(tmp_path) == model_profiles.DEFAULT_MAX_TURNS  # never measured

    fast = Summary(
        variant="baseline", runs=3, pass_rate=1.0, citation_rate=1.0, mean_turns=1.0,
        mean_tokens_out=1, mean_elapsed_s=1, stdev_elapsed_s=0, tokens_per_s=1,
    )  # fmt: skip
    save_model_profile(Backend(name="local", base_url="http://x", model="m"), fast, [])
    _looping_model(fake_client, monkeypatch, steps=20)
    assert _turns_used(tmp_path) == model_profiles.MIN_MAX_TURNS  # a fast model gets a short leash
    _looping_model(fake_client, monkeypatch, steps=20)
    assert _turns_used(tmp_path, max_turns=6) == 6  # an explicit argument always wins


def _project_profile(root: Path, name: str, front: str) -> None:
    folder = root / ".deputy" / "profiles"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: test\n{front}\n---\nYou help.\n"
    )


def _delegate_with(tmp_path: Path, profile: str, **arguments: Any) -> dict[str, Any]:
    out = asyncio.run(
        build_server().call_tool(
            "delegate", {"task": "go", "workdir": str(tmp_path), "profile": profile, **arguments}
        )
    )
    return structured(out)


def test_a_profile_sets_the_turn_limit_and_an_explicit_argument_still_wins(
    tmp_path: Path,
    fake_client,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    _project_profile(tmp_path, "tight", "max_turns: 3")
    _looping_model(fake_client, monkeypatch, steps=20)
    _delegate_with(tmp_path, "tight")
    assert _last_metrics()["turns"] == 3
    _looping_model(fake_client, monkeypatch, steps=20)
    _delegate_with(tmp_path, "tight", max_turns=5)
    assert _last_metrics()["turns"] == 5
    _project_profile(tmp_path, "plain", "")
    _looping_model(fake_client, monkeypatch, steps=20)
    _delegate_with(tmp_path, "plain")
    assert _last_metrics()["turns"] == model_profiles.DEFAULT_MAX_TURNS


def test_a_profile_can_turn_the_correction_round_off(
    tmp_path: Path,
    fake_client,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    checks = [{"type": "contains", "values": ["beta"]}]
    replies = [message("nothing useful"), message("beta")]  # wrong, then right after correction

    def run(profile: str) -> dict[str, Any]:
        monkeypatch.setattr(server, "ChatClient", lambda backend: fake_client(list(replies)))
        return _delegate_with(tmp_path, profile, checks=checks)

    _project_profile(tmp_path, "strict", "max_retries: 0")
    _project_profile(tmp_path, "forgiving", "max_retries: 2")
    _project_profile(tmp_path, "plain", "")
    assert run("strict")["status"] == "checks_failed"  # no second chance
    assert run("plain")["status"] == "ok"  # the default: one correction round
    run("forgiving")
    assert _last_metrics()["retries"] == 1  # a limit is a ceiling, not a quota


def test_doctor_reports_what_is_wrong_in_a_profile_front_matter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    _project_profile(tmp_path, "oops", "max_turns: lots")
    monkeypatch.chdir(tmp_path)

    async def healthy(self):  # type: ignore[no-untyped-def]
        return Probe(True, "ok")

    monkeypatch.setattr("deputy.backends.ChatClient.probe", healthy)
    cli.main(["doctor"])
    assert (
        "profile oops: max_turns: 'lots' is not a whole number (ignored)" in capsys.readouterr().out
    )


def test_a_delegation_naming_only_a_short_file_carries_the_small_input_flag(
    tmp_path: Path,
    fake_client,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    (tmp_path / "notes.md").write_text("alpha beta\n")
    monkeypatch.setattr(server, "ChatClient", lambda backend: fake_client([message("beta")]))
    out = asyncio.run(
        build_server().call_tool(
            "delegate",
            {
                "task": "find beta in notes.md",
                "workdir": str(tmp_path),
                "checks": [{"type": "contains", "values": ["beta"]}],
            },
        )
    )
    flags = structured(out)["flags"]
    assert any(flag.startswith("small_input") for flag in flags)
