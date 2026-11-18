# pyright: basic
import asyncio
import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from deputy import cli, runs, stats
from deputy.agent import run_task, task_class
from deputy.checks import CitesLines, Contains, FileWritten
from deputy.config import Policy, state_dir
from deputy.profiles import load_profiles
from deputy.sandbox import Sandbox
from helpers import message

NOW = 1_800_000_000.0
DAY = stats.SECONDS_PER_DAY


def run_event(
    run_id: str,
    *,
    age_days: float = 0.0,
    status: str = "ok",
    saved: int = 100,
    profile: str = "helper",
    task_class_: str = "helper:contains",
    retries: int = 0,
    flags: list[str] | None = None,
    elapsed: float = 10.0,
) -> dict:
    return {
        "event": "run", "run_id": run_id, "ts": NOW - age_days * DAY, "profile": profile,
        "task_class": task_class_, "status": status, "flags": flags or [], "saved_est": saved,
        "metrics": {"retries": retries, "elapsed_s": elapsed},
    }  # fmt: skip


def feedback(run_id: str, verdict: str) -> dict:
    return {"event": "feedback", "run_id": run_id, "verdict": verdict, "ts": NOW}


def test_the_report_counts_outcomes_acceptance_and_savings() -> None:
    events = [
        run_event("a", saved=300),
        run_event("b", saved=100, retries=1, elapsed=20),
        run_event("c", status="checks_failed", flags=["needs_frontier"], saved=0),
        feedback("a", "accepted"),
        feedback("b", "rejected"),
    ]
    total = stats.build_report(events, days=30, now=NOW).total
    assert (total.runs, total.ok, total.escalated, total.retried) == (3, 2, 1, 1)
    assert (total.accepted, total.rejected) == (1, 1)
    assert total.acceptance_rate == 0.5
    assert total.tokens_saved_est == 400
    assert total.pass_rate == pytest.approx(2 / 3)
    assert total.mean_seconds == pytest.approx(40 / 3)


def test_only_the_requested_window_counts_and_the_last_verdict_wins() -> None:
    events = [
        run_event("new", age_days=1),
        run_event("old", age_days=40),
        feedback("new", "rejected"),
        feedback("new", "accepted"),
    ]
    total = stats.build_report(events, days=7, now=NOW).total
    assert total.runs == 1
    assert (total.accepted, total.rejected) == (1, 0)


def test_runs_are_grouped_by_profile_and_by_task_class() -> None:
    events = [
        run_event("a", profile="helper", task_class_="helper:contains"),
        run_event("b", profile="helper", task_class_="helper:contains"),
        run_event("c", profile="voice-dev", task_class_="voice-dev:file_written"),
    ]
    report = stats.build_report(events, days=30, now=NOW)
    assert [(g.key, g.runs) for g in report.by_profile] == [("helper", 2), ("voice-dev", 1)]
    assert (report.by_class[0].key, report.by_class[0].runs) == ("helper:contains", 2)


def test_old_trace_lines_without_the_new_fields_still_count() -> None:
    legacy = {"event": "run", "run_id": "x", "ts": NOW, "profile": "helper", "status": "ok",
              "metrics": {"elapsed_s": 5}, "saved_est": 50}  # fmt: skip
    report = stats.build_report([legacy], days=30, now=NOW)
    assert report.by_class[0].key == "helper:unknown"
    assert report.total.tokens_saved_est == 50


def test_the_task_class_names_the_profile_and_the_kinds_of_checks() -> None:
    checks = [Contains(values=["a"]), CitesLines(max=3), Contains(values=["b"])]
    assert task_class("helper", checks) == "helper:cites_lines+contains"
    assert task_class("helper", [FileWritten(path="x")]) == "helper:file_written"
    assert task_class("helper", []) == "helper:no-checks"


def test_a_real_run_leaves_a_trace_that_the_report_reads(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]

    result = asyncio.run(
        run_task(
            client=fake_client([message("beta, line 2")]),
            profile=load_profiles()["helper"],
            task="where?",
            sandbox=Sandbox(tmp_path),
            checks=[CitesLines(max=3)],
            policy=Policy(),
        )
    )
    runs.record({"event": "feedback", "run_id": result.run_id, "verdict": "accepted"})
    events, unreadable = stats.read_trace()
    report = stats.build_report(events, days=1, unreadable=unreadable)
    assert report.total.runs == 1
    assert report.total.accepted == 1
    assert report.by_class[0].key == "helper:cites_lines"


def test_unreadable_lines_are_counted_not_fatal() -> None:
    path = state_dir() / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"event": "run", "run_id": "a", "ts": 1}\nnot json\n[1, 2]\n')
    events, unreadable = stats.read_trace()
    assert len(events) == 1
    assert unreadable == 2


def test_the_text_report_has_the_numbers_and_says_when_there_is_nothing() -> None:
    assert "no runs" in stats.format_report(stats.build_report([], days=7, now=NOW))
    events = [run_event("a", saved=1234), feedback("a", "accepted")]
    text = stats.format_report(stats.build_report(events, days=30, now=NOW))
    assert "1234" in text
    assert "100%" in text
    assert "by task class" in text
    assert "helper:contains" in text


def test_the_status_line_is_absent_without_runs_and_short_with_them() -> None:
    assert stats.usage_line() is None
    runs.record({"event": "run", "run_id": "a", "profile": "helper", "status": "ok",
                 "metrics": {}, "saved_est": 700})  # fmt: skip
    line = stats.usage_line()
    assert line is not None
    assert "1 runs" in line
    assert "~700 tokens saved" in line


def test_the_stats_command_prints_a_table_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    runs.record({"event": "run", "run_id": "a", "profile": "helper", "status": "ok",
                 "task_class": "helper:contains", "metrics": {"elapsed_s": 3},
                 "saved_est": 90})  # fmt: skip
    assert cli.main(["stats"]) == 0
    assert "helper:contains" in capsys.readouterr().out
    assert cli.main(["stats", "--days", "1", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total"]["runs"] == 1
    assert data["total"]["tokens_saved_est"] == 90


@given(
    st.lists(
        st.tuples(st.sampled_from(["ok", "checks_failed", "error"]), st.integers(0, 5000),
                  st.sampled_from([None, "accepted", "rejected"])),
        max_size=30,
    )
)  # fmt: skip
def test_the_totals_always_add_up(items) -> None:  # type: ignore[no-untyped-def]
    events: list[dict] = []
    for index, (status, saved, verdict) in enumerate(items):
        events.append(run_event(str(index), status=status, saved=saved))
        if verdict:
            events.append(feedback(str(index), verdict))
    report = stats.build_report(events, days=30, now=NOW)
    assert report.total.runs == len(items)
    assert report.total.ok == sum(1 for status, _, _ in items if status == "ok")
    assert report.total.tokens_saved_est == sum(saved for _, saved, _ in items)
    assert sum(g.runs for g in report.by_profile) == report.total.runs
    assert sum(g.runs for g in report.by_class) == report.total.runs
    assert report.total.accepted + report.total.rejected == sum(1 for _, _, v in items if v)
