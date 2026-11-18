# pyright: basic
"""Each benchmark task must pass on a perfect answer and fail on the typical wrong ones: a grader
that cannot fail measures nothing."""

import asyncio
import sys
from pathlib import Path

import pytest

from deputy.bench import (
    HUGE_ERRORS,
    NOTICE_IT,
    TEST_FILE,
    BenchTask,
    Variant,
    _policy_for,
    build_project,
    huge_log,
    run_suite,
    runnable,
    selection_names,
    tasks,
    tasks_with_kind,
)
from deputy.checks import CheckContext, run_checks
from deputy.config import Backend, Policy
from deputy.sandbox import LARGE_FILE_CHARS, Sandbox
from helpers import message, tool_call


def task(name: str) -> BenchTask:
    return next(t for t in tasks() if t.name == name)


def grade(name: str, root: Path, answer: str = "", written: list[str] | None = None) -> list[bool]:
    box = Sandbox(root)
    box.files_written.extend(written or [])
    results = asyncio.run(
        run_checks(task(name).checks, answer, box, _policy_for(task(name), Policy()))
    )
    return [r.passed for r in results]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    build_project(tmp_path)
    return tmp_path


def test_task_names_are_unique_and_the_code_task_is_marked() -> None:
    names = [t.name for t in tasks()]
    assert len(names) == len(set(names))
    assert [t.name for t in tasks() if t.executes_code] == ["write-passing-test"]


def test_short_with_facts_needs_both_periods_and_the_length_limit(project: Path) -> None:
    good = "Audit logs: 30 days, then backup. Invoices: 10 years in backup storage."
    assert all(grade("short-with-facts", project, good))
    assert not all(grade("short-with-facts", project, "Audit logs are kept 30 days."))
    assert not all(grade("short-with-facts", project, good + " x" * 200))


def test_translate_needs_the_file_and_the_identifiers_kept(project: Path) -> None:
    (project / "notice.en.txt").write_text(
        "The file runner_labels.py rejects pull_request_target after 30 days, as decided in #415.\n"
    )
    assert all(grade("translate-keep-terms", project, written=["notice.en.txt"]))
    (project / "notice.en.txt").write_text(
        "The file rejects the event after a month, as decided.\n"
    )
    assert not all(grade("translate-keep-terms", project, written=["notice.en.txt"]))
    assert not any(grade("translate-keep-terms", project, written=[]))  # nothing written
    assert "runner_labels.py" in NOTICE_IT  # the source really holds what must survive


def test_the_code_task_passes_only_with_a_test_that_exits_zero(project: Path) -> None:
    (project / TEST_FILE).write_text(
        "from slug import slugify\nassert slugify('Hello World') == 'hello-world'\n"
        "assert slugify('a  b') == 'a-b'\nassert slugify('X!') == 'x'\n"
    )
    assert all(grade("write-passing-test", project, written=[TEST_FILE]))
    (project / TEST_FILE).write_text(
        "from slug import slugify\nassert slugify('Hello') == 'nope'\n"
    )
    results = grade("write-passing-test", project, written=[TEST_FILE])
    assert results[:2] == [True, True]
    assert results[2] is False  # the command failed: the generated test does not pass


def test_the_code_task_runs_only_one_allowed_command() -> None:
    policy = _policy_for(task("write-passing-test"), Policy(allowed_commands=[["node", "--test"]]))
    assert [sys.executable, TEST_FILE] in policy.allowed_commands
    assert ["node", "--test"] in policy.allowed_commands
    assert len(policy.allowed_commands) == 2
    plain = _policy_for(task("write-file"), Policy())
    assert plain.allowed_commands == []


def test_the_suite_skips_code_tasks_unless_asked(fake_client) -> None:  # type: ignore[no-untyped-def]
    write_test = [
        message(
            tool_calls=[
                tool_call(
                    "write_file",
                    {
                        "path": TEST_FILE,
                        "content": "from slug import slugify\nassert slugify('A b') == 'a-b'\n"
                        "assert slugify('x') == 'x'\nassert slugify('Y!') == 'y'\n",
                    },
                )
            ]
        ),
        message("done"),
    ]
    seen: list[str] = []
    for run_code, expected in ((False, []), (True, ["write-passing-test"])):
        summary = asyncio.run(
            run_suite(
                Backend(name="b", base_url="http://x"),
                Variant(name="v"),
                runs=1,
                only=["write-passing-test"],
                run_code=run_code,
                client_factory=lambda backend: fake_client(list(write_test)),
                on_result=lambda name, r: seen.append(f"{name}:{r.status}"),
            )
        )
        assert summary.runs == len(expected)
    assert seen == ["write-passing-test:ok"]


def test_the_huge_file_is_larger_than_a_context_and_has_one_clear_winner() -> None:

    text = huge_log()
    assert len(text) > 130_000  # more than a 32k-token context at 4 characters a token
    assert len(text) > LARGE_FILE_CHARS  # so the worker has to scan it
    counts = {w: text.count(f"ERROR {w} ") for w in HUGE_ERRORS}
    assert counts == HUGE_ERRORS
    assert max(counts, key=counts.__getitem__) == "worker-3"
    assert text == huge_log()  # the same every time, so runs are comparable


def test_the_huge_file_task_grades_the_worker_and_the_count() -> None:

    task = next(t for t in tasks() if t.name == "count-in-huge-file")
    assert task.slow
    good, wrong_worker, wrong_count = (
        "worker-3 logged 9 ERROR lines",
        "worker-1 logged 9 ERROR lines",
        "worker-3 logged 4 ERROR lines",
    )
    for answer, passes in ((good, True), (wrong_worker, False), (wrong_count, False)):
        ctx = CheckContext(answer=answer, sandbox=None, policy=None)  # type: ignore[arg-type]
        outcomes = [asyncio.run(c.evaluate(ctx)) for c in task.checks]
        assert all(o.passed for o in outcomes) is passes


def test_slow_tasks_run_only_when_asked_and_never_in_tuning() -> None:

    assert "count-in-huge-file" not in [t.name for t in runnable()]
    assert "count-in-huge-file" in [t.name for t in runnable(slow=True)]
    assert "count-in-huge-file" not in selection_names()
    assert "count-in-huge-file" not in tasks_with_kind("contains")
