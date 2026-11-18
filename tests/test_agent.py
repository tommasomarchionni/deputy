# pyright: basic
import asyncio
import subprocess as sp
from pathlib import Path

from deputy import agent
from deputy.agent import Result, run_task
from deputy.checks import CheckResult, CitesLines, Contains
from deputy.config import Policy
from deputy.profiles import Profile
from deputy.sandbox import Sandbox
from helpers import message, tool_call

PROFILE = Profile(name="helper", description="", body="You help.", source="test")


def go(client, tmp_path: Path, script_checks=None, **kw):  # type: ignore[no-untyped-def]
    box = Sandbox(tmp_path)
    return asyncio.run(
        run_task(
            client=client,
            profile=PROFILE,
            task="summarise a.txt",
            sandbox=box,
            checks=script_checks or [],
            policy=Policy(),
            **kw,
        )
    )


def test_reads_a_file_then_answers_and_estimates_the_saving(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "a.txt").write_text("\n".join(f"line {i} " + "x" * 80 for i in range(200)))
    client = fake_client(
        [
            message(tool_calls=[tool_call("read_file", {"path": "a.txt", "limit": 200})]),
            message("done"),
        ]
    )
    result = go(client, tmp_path)
    assert result.status == "ok"
    assert result.answer == "done"
    assert result.metrics.turns == 2
    assert result.metrics.tool_calls == 1
    assert result.orchestrator_tokens_saved_est > 1000


def test_a_failing_check_gets_one_correction_then_passes(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    client = fake_client([message("no citation"), message("fixed, line 5")])
    result = go(client, tmp_path, [CitesLines(min=1, max=10)])
    assert result.status == "ok"
    assert result.metrics.retries == 1


def test_checks_that_keep_failing_hand_the_task_back(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    client = fake_client([message("a"), message("b")])
    result = go(client, tmp_path, [Contains(values=["needle"])])
    assert result.status == "checks_failed"
    assert "needs_frontier" in result.flags


def test_a_refused_path_is_reported_to_the_model_not_raised(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    client = fake_client(
        [
            message(tool_calls=[tool_call("read_file", {"path": "../../etc/passwd"})]),
            message("refused"),
        ]
    )
    result = go(client, tmp_path)
    assert result.status == "ok"
    assert result.files_written == []


def test_unknown_tools_and_bad_arguments_do_not_crash(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    calls = [tool_call("run_bash", {"command": "ls"}, "c1"), tool_call("read_file", {}, "c2")]
    client = fake_client([message(tool_calls=calls), message("ok")])
    assert go(client, tmp_path).status == "ok"


def test_the_turn_limit_stops_a_looping_model(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    loop = message(tool_calls=[tool_call("grep", {"pattern": "x"})])
    result = go(fake_client([loop, loop, loop]), tmp_path, max_turns=3)
    assert result.status == "turn_limit"
    assert "turn_limit" in result.flags


def test_a_backend_failure_becomes_an_error_result(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    result = go(fake_client([500]), tmp_path)
    assert result.status == "error"
    assert "backend_error" in result.flags


def test_the_worker_can_write_a_file_in_the_sandbox(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    call = tool_call("write_file", {"path": "out.txt", "content": "hello"})
    result = go(fake_client([message(tool_calls=[call]), message("written")]), tmp_path)
    assert result.files_written == ["out.txt"]
    assert (tmp_path / "out.txt").read_text() == "hello"


def test_a_long_failed_answer_is_returned_as_a_short_excerpt() -> None:
    long = "x" * 2_000
    out = agent.excerpt_of_failed(long)
    assert len(out) < 400
    assert out.endswith("failed its checks]")


def test_a_short_failed_answer_is_returned_whole() -> None:
    assert agent.excerpt_of_failed("no") == "no"


def test_the_result_of_a_failed_run_carries_a_short_answer(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    client = fake_client([message("a" * 2_000), message("b" * 2_000)])
    result = go(client, tmp_path, [Contains(values=["needle"])])
    assert result.status == "checks_failed"
    assert len(result.answer) < 400
    assert not result.checks[0].passed


def test_the_result_of_a_passing_run_keeps_its_whole_long_answer(
    fake_client, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    long = "needle " + "a" * 2_000
    result = go(fake_client([message(long)]), tmp_path, [Contains(values=["needle"])])
    assert result.status == "ok"
    assert result.answer == long


def test_the_worker_can_ask_for_a_log_summary(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "app.log").write_text("\n".join(f"INFO batch {i} ok" for i in range(500)))
    client = fake_client(
        [
            message(tool_calls=[tool_call("summarise_log", {"path": "app.log"})]),
            message("one kind of line, 500 times"),
        ]
    )
    result = go(client, tmp_path)
    assert result.status == "ok"
    assert result.metrics.tool_calls == 1
    assert result.orchestrator_tokens_saved_est > 1000


def _result(**over):  # type: ignore[no-untyped-def]
    base = {
        "run_id": "r1",
        "status": "ok",
        "answer": "beta",
        "checks": [
            CheckResult(name="contains", passed=True),
            CheckResult(name="max_chars", passed=True),
        ],
    }
    return Result(**{**base, **over})


def test_a_passing_outcome_is_the_answer_a_summary_and_nothing_else() -> None:
    out = agent.outcome_of(_result())
    assert out.checks == "2/2 passed"
    assert out.model_dump_json(exclude_none=True) == (
        '{"run_id":"r1","status":"ok","answer":"beta","checks":"2/2 passed"}'
    )


def test_a_failing_outcome_names_what_failed_with_its_detail() -> None:
    failed = [CheckResult(name="contains", passed=False, detail="missing: needle")]
    out = agent.outcome_of(_result(status="checks_failed", checks=failed, flags=["needs_frontier"]))
    assert out.checks == "0/1 passed; failed: contains: missing: needle"
    assert out.flags == ["needs_frontier"]


def test_an_outcome_with_files_and_no_checks_keeps_only_what_exists() -> None:
    out = agent.outcome_of(_result(checks=[], files_written=["out.txt"]))
    assert out.checks is None
    assert out.files_written == ["out.txt"]
    assert out.flags is None


def test_the_worker_can_read_git_history(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    setup = (["init", "-q"], ["config", "user.email", "t@e.com"], ["config", "user.name", "T"])
    for args in setup:
        sp.run(["git", "-C", str(tmp_path), *args], check=True)
    (tmp_path / "a.txt").write_text("one\n")
    sp.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "add a"], check=True)
    client = fake_client(
        [
            message(tool_calls=[tool_call("git", {"args": ["log", "--oneline"]})]),
            message("one commit: add a"),
        ]
    )
    result = go(client, tmp_path, [Contains(values=["add a"])])
    assert result.status == "ok"
    assert result.metrics.tool_calls == 1
