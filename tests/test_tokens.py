# pyright: basic
import asyncio
import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from deputy import stats, tokens
from deputy.agent import run_task
from deputy.checks import Contains
from deputy.config import Policy, state_dir
from deputy.profiles import load_profiles
from deputy.sandbox import Sandbox
from helpers import message, tool_call


def test_the_estimator_is_bytes_over_four_rounded_up() -> None:
    assert tokens.estimate("") == 0
    assert tokens.estimate("abcd") == 1
    assert tokens.estimate("abcde") == 2
    assert tokens.estimate("è" * 4) == 2  # two bytes each in UTF-8
    assert tokens.from_chars(9) == 3


def test_a_value_is_estimated_as_the_compact_json_the_orchestrator_sees() -> None:
    value = {"task": "x" * 40, "checks": [{"type": "contains"}]}
    assert tokens.estimate_value(value) == tokens.estimate(json.dumps(value, separators=(",", ":")))


@given(st.text(max_size=300), st.text(max_size=300))
def test_the_estimate_never_shrinks_when_text_is_added(a: str, b: str) -> None:
    assert tokens.estimate(a + b) >= tokens.estimate(a)
    assert tokens.estimate(a) >= 0


def run(fake_client, tmp_path: Path, script, **kw):  # type: ignore[no-untyped-def]
    return asyncio.run(
        run_task(
            client=fake_client(script), profile=load_profiles()["helper"], task="read a.txt",
            sandbox=Sandbox(tmp_path), checks=[Contains(values=["alpha"])], policy=Policy(), **kw,
        )
    )  # fmt: skip


def trace() -> list[dict]:
    return [json.loads(x) for x in (state_dir() / "runs.jsonl").read_text().splitlines()]


def last_run() -> dict:
    return [e for e in trace() if e["event"] == "run"][-1]


def test_saving_is_what_the_worker_read_minus_what_the_orchestrator_paid(  # type: ignore[no-untyped-def]
    fake_client, tmp_path: Path
) -> None:
    (tmp_path / "a.txt").write_text("alpha line\n" * 2000)  # about 22,000 characters
    script = [
        message(tool_calls=[tool_call("read_file", {"path": "a.txt", "limit": 2000})]),
        message("alpha"),
    ]
    result = run(fake_client, tmp_path, script, request_tokens=300)
    event = last_run()
    assert event["read_est"] > 5000
    assert event["cost_est"] >= 300  # at least the request it sent
    assert (
        event["saved_est"]
        == event["read_est"] - event["cost_est"]
        == result.orchestrator_tokens_saved_est
    )
    assert result.orchestrator_tokens_saved_est > 4000


def test_a_delegation_that_reads_little_shows_a_negative_saving(  # type: ignore[no-untyped-def]
    fake_client, tmp_path: Path
) -> None:
    result = run(fake_client, tmp_path, [message("alpha")], request_tokens=400)  # reads nothing
    assert result.orchestrator_tokens_saved_est < 0
    assert last_run()["saved_est"] == result.orchestrator_tokens_saved_est


def test_the_default_request_cost_is_the_size_of_the_task(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    run(fake_client, tmp_path, [message("alpha")])
    assert last_run()["cost_est"] >= tokens.estimate("read a.txt")


def test_the_report_shows_read_and_cost_per_run_and_the_json_has_them() -> None:
    events = [
        {
            "event": "run",
            "run_id": f"r{i}",
            "ts": 1e9,
            "profile": "helper",
            "status": "ok",
            "metrics": {},
            "read_est": 3000,
            "cost_est": 400,
            "saved_est": 2600,
        }
        for i in range(2)
    ]
    total = stats.build_report(events, days=1e6, now=1e9 + 10).total
    assert (total.mean_read, total.mean_cost, total.mean_saved) == (3000, 400, 2600)
    assert total.as_dict()["mean_cost"] == 400
    text = stats.format_report(stats.build_report(events, days=1e6, now=1e9 + 10))
    assert "read" in text
    assert "cost" in text
    assert "3000" in text


def test_a_trace_line_from_before_the_accounting_still_counts() -> None:
    legacy = {"event": "run", "run_id": "x", "ts": 1e9, "profile": "helper", "status": "ok",
              "metrics": {}, "saved_est": 50}  # fmt: skip
    total = stats.build_report([legacy], days=1e6, now=1e9 + 10).total
    assert total.runs == 1
    assert (total.mean_read, total.mean_cost, total.tokens_saved_est) == (0, 0, 0)


def test_a_failed_run_records_a_loss_of_its_cost(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "a.txt").write_text("alpha line\n" * 2000)
    script = [
        message(tool_calls=[tool_call("read_file", {"path": "a.txt", "limit": 2000})]),
        message("no"),
        message("still no"),
    ]
    result = run(fake_client, tmp_path, script, request_tokens=300)  # neither answer says "alpha"
    event = last_run()
    assert result.status == "checks_failed"
    assert event["read_est"] > 5000  # it read a lot
    assert result.orchestrator_tokens_saved_est == event["saved_est"] == -event["cost_est"]


def test_a_handed_back_run_records_a_rejection_by_itself(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    result = run(fake_client, tmp_path, [message("no"), message("still no")])
    assert result.status == "checks_failed"
    verdict = trace()[-1]
    assert verdict["event"] == "feedback"
    assert verdict["run_id"] == result.run_id
    assert verdict["verdict"] == "rejected"
    assert verdict["source"] == "auto"
    assert verdict["reason"].startswith("handed back, checks failed: contains")


def test_a_passing_run_and_a_benchmark_run_get_no_automatic_verdict(
    fake_client, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    run(fake_client, tmp_path, [message("alpha")])
    assert trace()[-1]["event"] == "run"
    run(fake_client, tmp_path, [message("no"), message("no")], source="bench")
    assert trace()[-1]["event"] == "run"
