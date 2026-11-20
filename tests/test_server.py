# pyright: basic
import asyncio
import json
import time
from pathlib import Path
from typing import Any, cast

import pytest

from deputy import cli, confinement, localagent, model_profiles, server, tokens
from deputy.agent import Result
from deputy.backends import Probe
from deputy.bench import Summary
from deputy.config import Backend, ConfigError, config_path, state_dir
from deputy.server import build_server
from deputy.tune import save_model_profile
from helpers import message, tool_call

SCHEMA_BUDGET_CHARS = 5_000  # about 1,250 tokens: what the orchestrator carries on every turn


def structured(result: Any) -> dict[str, Any]:
    """The structured payload of a tool call (the SDK types the result as a union)."""
    return result.structured_content


def test_exposes_exactly_six_small_tools() -> None:
    tools = asyncio.run(build_server().list_tools())
    assert sorted(t.name for t in tools) == [
        "collect",
        "delegate",
        "digest",
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
    assert "helper" in json.loads(cast("Any", profiles).content[0].text)
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
            "saved_est": history_saved, "read_est": history_saved + 100, "cost_est": 100,
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


def test_a_passing_result_sent_over_the_tool_call_has_no_empty_fields(
    tmp_path: Path,
    fake_client,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
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
    assert set(structured(out)) == {"run_id", "status", "answer", "checks"}


def _digest(tmp_path: Path, **overrides: object) -> dict:
    arguments = {"files": ["a.md"], "question": "What is beta?", "workdir": str(tmp_path)}
    out: Any = asyncio.run(build_server().call_tool("digest", {**arguments, **overrides}))
    return json.loads(out.content[0].text)


def test_digest_answers_with_proven_citations(
    tmp_path: Path,
    fake_client,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    (tmp_path / "a.md").write_text("alpha\nbeta is the second letter\n")
    script = [
        message(tool_calls=[tool_call("read_file", {"path": "a.md"})]),
        message('- beta comes second (a.md:2 "beta is the second letter")'),
    ]
    monkeypatch.setattr(server, "ChatClient", lambda backend: fake_client(script))
    data = _digest(tmp_path)
    assert data["status"] == "ok"
    assert data["checks"] == "2/2 passed"


def test_digest_hands_back_an_answer_quoting_words_the_line_does_not_hold(
    tmp_path: Path,
    fake_client,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    (tmp_path / "a.md").write_text("alpha\nbeta is the second letter\n")
    wrong = message('- beta comes first (a.md:1 "beta is the first letter")')
    monkeypatch.setattr(server, "ChatClient", lambda backend: fake_client([wrong, wrong]))
    data = _digest(tmp_path)
    assert data["status"] == "checks_failed"
    assert "needs_frontier" in data["flags"]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"files": ["missing.md"]}, "not a file"),
        ({"files": ["../outside.md"]}, "outside the working directory"),
        ({"files": [".env"]}, "not accessible"),
        ({"files": ["image.bin"]}, "not a UTF-8 text file"),
        ({"files": ["empty.md"]}, "empty file"),
        ({"files": []}, "between 1 and 8"),
        ({"files": ["a.md"] * 9}, "between 1 and 8"),
        ({"question": "  "}, "question is empty"),
    ],
)
def test_digest_refuses_bad_requests_before_any_model_call(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
    overrides: dict,
    expected: str,
) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    (tmp_path / "a.md").write_text("alpha\n")
    (tmp_path / "empty.md").write_text("")
    (tmp_path / ".env").write_text("KEY=1\n")
    (tmp_path / "image.bin").write_bytes(b"\xff\xfe\x00\x01")

    def no_model(backend: object) -> None:
        raise AssertionError("no model may be called")

    monkeypatch.setattr(server, "ChatClient", no_model)
    data = _digest(tmp_path, **overrides)
    assert data["status"] == "error"
    assert expected in data["answer"]


def test_delegate_starts_a_local_agent_in_the_background(tmp_path: Path, monkeypatch) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    seen = {}

    async def run(task, workdir, backend, checks, policy, run_id, steps):
        seen.update(task=task, workdir=workdir, model=backend.model, steps=steps)
        return Result(run_id=run_id, status="ok", answer="patch ready")

    async def enough(backend) -> None:
        return None

    monkeypatch.setattr(localagent, "run", run)
    monkeypatch.setattr(localagent, "require_context", enough)

    async def go() -> tuple[dict, dict]:
        tools: Any = build_server()
        arguments = {
            "task": "change it",
            "workdir": str(tmp_path),
            "agent": True,
            "steps": ["one", "two"],
        }
        started = structured(await tools.call_tool("delegate", arguments))
        done = await tools.call_tool("collect", {"run_id": started["run_id"]})
        return started, json.loads(done.content[0].text)

    started, done = asyncio.run(go())
    assert started["status"] == "running"
    assert done["answer"] == "patch ready"
    assert seen == {"task": "change it", "workdir": tmp_path, "model": "m", "steps": ["one", "two"]}


def test_a_local_agent_is_refused_when_the_context_is_too_small(
    tmp_path: Path, monkeypatch
) -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])

    async def small(backend) -> None:
        raise ConfigError("restart it with --ctx-size 131072")

    monkeypatch.setattr(localagent, "require_context", small)
    arguments = {"task": "change it", "workdir": str(tmp_path), "agent": True}
    data = structured(asyncio.run(build_server().call_tool("delegate", arguments)))
    assert data["status"] == "error"
    assert "--ctx-size" in data["answer"]


def test_a_local_agent_is_refused_before_it_starts_when_a_check_command_is_not_allowed(
    tmp_path: Path, monkeypatch
) -> None:
    # without a sandbox tool the checks run with the user's rights, so the allow-list decides
    monkeypatch.setattr(confinement, "agent_wrapper_tool", lambda: None)
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    arguments = {
        "task": "change it",
        "workdir": str(tmp_path),
        "agent": True,
        "checks": [{"type": "command", "argv": ["uv", "run", "pytest"]}],
    }
    data = structured(asyncio.run(build_server().call_tool("delegate", arguments)))
    assert data["status"] == "error"
    assert "does not allow the check command(s) uv run pytest" in data["answer"]
    assert "allowed_commands" in data["answer"]


def test_with_a_sandbox_tool_the_checks_of_a_local_agent_need_no_allow_list(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(confinement, "agent_wrapper_tool", lambda: "/usr/bin/sandbox-exec")
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])

    async def run(task, workdir, backend, checks, policy, run_id, steps):
        return Result(run_id=run_id, status="ok", answer=f"{len(checks)} check(s)")

    async def enough(backend) -> int:
        return 131_072

    monkeypatch.setattr(localagent, "run", run)
    monkeypatch.setattr(localagent, "require_context", enough)
    arguments = {
        "task": "change it",
        "workdir": str(tmp_path),
        "agent": True,
        "checks": [{"type": "command", "argv": ["uv", "run", "pytest"]}],
    }
    data = structured(asyncio.run(build_server().call_tool("delegate", arguments)))
    assert data["status"] == "running"
    assert "deputy wait" in data["answer"]


def test_with_a_local_backend_every_session_is_told_to_hand_code_changes_to_the_agent() -> None:
    assert server.instructions() == server.INSTRUCTIONS  # no configuration yet
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    told = server.instructions()
    assert told.startswith(server.INSTRUCTIONS)
    for rule in ("agent=true", "do not read or edit the code first", "`steps`", "deputy wait"):
        assert rule in told
    assert tokens.estimate(told) <= 400  # every session carries it


def test_the_user_can_keep_code_changes_for_the_frontier_model() -> None:
    cli.main(["init", "--yes", "--url", "http://x", "--model", "m"])
    path = config_path()
    path.write_text('code_changes = "frontier"\n' + path.read_text())
    assert server.instructions() == server.INSTRUCTIONS
