# pyright: basic
import asyncio
import json
import time
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from deputy import cli, runs, stats
from deputy.agent import run_task, task_class
from deputy.bench import Variant, run_suite
from deputy.checks import CitesLines, Contains, FileWritten
from deputy.config import Backend, Policy, state_dir
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
        "read_est": max(saved, 0) + 100, "cost_est": max(saved, 0) + 100 - saved,
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
    # The failed run spared nothing: it counts as minus its cost (100 in run_event), a loss.
    assert total.tokens_saved_est == 300 + 100 - 100
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
    assert report.total.runs == 1  # still counted as a run
    assert report.total.tokens_saved_est == 0  # but its old, not comparable estimate is not summed
    assert "1 run(s) from older versions" in stats.format_report(report)


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
                 "metrics": {}, "saved_est": 700, "read_est": 800, "cost_est": 100})  # fmt: skip
    line = stats.usage_line()
    assert line is not None
    assert "1 runs" in line
    assert "~700 tokens saved" in line


def test_the_stats_command_prints_a_table_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    runs.record({"event": "run", "run_id": "a", "profile": "helper", "status": "ok",
                 "task_class": "helper:contains", "metrics": {"elapsed_s": 3},
                 "saved_est": 90, "read_est": 190, "cost_est": 100})  # fmt: skip
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
    expected = sum(saved if status == "ok" else -100 for status, saved, _ in items)
    assert report.total.tokens_saved_est == expected  # a failed run counts as minus its cost
    assert sum(g.runs for g in report.by_profile) == report.total.runs
    assert sum(g.runs for g in report.by_class) == report.total.runs
    assert report.total.accepted + report.total.rejected == sum(1 for _, _, v in items if v)


def test_benchmark_runs_are_left_out_unless_asked_and_old_lines_count_as_real_use() -> None:
    real = run_event("real")
    real["source"] = "mcp"
    synthetic = run_event("synthetic")
    synthetic["source"] = "bench"
    legacy = run_event("legacy")  # an older trace line: no source
    events = [real, synthetic, legacy, feedback("synthetic", "rejected")]
    assert stats.build_report(events, days=30, now=NOW).total.runs == 2
    everything = stats.build_report(events, days=30, now=NOW, include_bench=True).total
    assert everything.runs == 3
    assert everything.rejected == 1


def test_the_benchmark_tags_its_runs_and_a_delegation_tags_itself_as_real_use(
    fake_client, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    asyncio.run(
        run_suite(
            Backend(name="b", base_url="http://x"),
            Variant(name="v"),
            runs=1,
            only=["short-summary"],
            client_factory=lambda backend: fake_client([message("short")]),
        )
    )
    asyncio.run(
        run_task(
            client=fake_client([message("done")]),
            profile=load_profiles()["helper"],
            task="x",
            sandbox=Sandbox(tmp_path),
            checks=[],
            policy=Policy(),
        )
    )
    events, _ = stats.read_trace()
    assert [e["source"] for e in events if e["event"] == "run"] == ["bench", "mcp"]
    assert stats.build_report(events, days=1).total.runs == 1
    assert stats.build_report(events, days=1, include_bench=True).total.runs == 2


def test_the_stats_command_can_include_the_benchmark(capsys: pytest.CaptureFixture[str]) -> None:
    runs.record({"event": "run", "run_id": "b", "profile": "helper", "status": "ok",
                 "source": "bench", "metrics": {}, "saved_est": 5})  # fmt: skip
    assert cli.main(["stats", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["total"]["runs"] == 0
    assert cli.main(["stats", "--json", "--include-bench"]) == 0
    assert json.loads(capsys.readouterr().out)["total"]["runs"] == 1


def runs_of(task_class_: str, count: int, **kwargs) -> list[dict]:  # type: ignore[no-untyped-def]
    return [
        run_event(f"{task_class_}-{i}", task_class_=task_class_, **kwargs) for i in range(count)
    ]


def advice_for_events(events: list[dict]) -> list[stats.Advice]:
    return stats.delegation_advice(stats.build_report(events, days=30, now=NOW))


def test_there_is_no_advice_until_a_class_has_been_tried_enough() -> None:
    few = runs_of("helper:contains", stats.MIN_RUNS_FOR_ADVICE - 1, saved=0)
    assert advice_for_events(few) == []


def test_a_class_with_no_net_saving_is_not_worth_delegating() -> None:
    (advice,) = advice_for_events(runs_of("helper:contains", 5, saved=0))
    assert advice.task_class == "helper:contains"
    assert advice.runs == 5
    assert "no net token saving" in advice.reason


def test_a_class_that_is_mostly_handed_back_is_not_worth_delegating() -> None:
    events = runs_of(
        "helper:cites_lines", 3, saved=500, status="checks_failed", flags=["needs_frontier"]
    )
    events += runs_of("helper:cites_lines", 2, saved=500)
    events = [{**e, "run_id": f"r{i}"} for i, e in enumerate(events)]
    (advice,) = advice_for_events(events)
    assert advice.escalation_rate == pytest.approx(0.6)
    assert "handed back" in advice.reason


def test_a_healthy_class_gets_no_advice_and_each_class_is_judged_alone() -> None:
    events = runs_of("helper:contains", 8, saved=400) + runs_of("helper:max_chars", 6, saved=-10)
    advice = advice_for_events(events)
    assert [a.task_class for a in advice] == ["helper:max_chars"]


def test_benchmark_traffic_never_triggers_advice() -> None:
    events = [{**e, "source": "bench"} for e in runs_of("helper:contains", 9, saved=0)]
    assert advice_for_events(events) == []


def test_the_advice_is_found_by_class_and_listed_for_the_status_tool() -> None:
    path = state_dir() / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    lines = [{**e, "ts": now} for e in runs_of("helper:contains", 6, saved=0)]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    assert stats.advice_for_class("helper:contains") is not None
    assert stats.advice_for_class("helper:other") is None
    (line,) = stats.advice_lines()
    assert line.startswith("helper:contains: 6 runs, no net token saving")
    assert line.endswith("do these yourself")


def test_an_old_inflated_run_does_not_swamp_the_saving() -> None:
    # The real case: one run from an old version reported 34,705,913 tokens with no breakdown.
    old = {"event": "run", "run_id": "old", "ts": NOW, "profile": "helper", "status": "ok",
           "metrics": {}, "saved_est": 34_705_913}  # fmt: skip
    report = stats.build_report([old, run_event("new", saved=4_000)], days=30, now=NOW)
    assert report.total.runs == 2
    assert report.total.tokens_saved_est == 4_000
    assert report.total.mean_saved == 4_000


def test_a_handed_back_run_is_a_loss_even_if_it_read_a_lot() -> None:
    # The real case: a summary handed back after reading 8,754 tokens was shown as +8,526.
    failed = run_event("x", status="checks_failed", flags=["needs_frontier"], saved=8_526)
    failed["read_est"], failed["cost_est"] = 8_754, 228
    report = stats.build_report([failed], days=30, now=NOW)
    assert report.total.tokens_saved_est == -228


def test_the_status_line_says_how_many_runs_were_judged() -> None:
    runs.record({"event": "run", "run_id": "a", "profile": "helper", "status": "ok", "metrics": {},
                 "saved_est": 700, "read_est": 800, "cost_est": 100})  # fmt: skip
    runs.record({"event": "run", "run_id": "b", "profile": "helper", "status": "ok", "metrics": {},
                 "saved_est": 700, "read_est": 800, "cost_est": 100})  # fmt: skip
    runs.record({"event": "feedback", "run_id": "a", "verdict": "accepted"})
    line = stats.usage_line()
    assert line is not None
    assert "(1 of 2 judged: send feedback)" in line
