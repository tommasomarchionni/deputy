# pyright: basic
import asyncio
import sys
from pathlib import Path

import pytest

from deputy.checks import (
    CitesLines,
    Command,
    Contains,
    Excludes,
    FileContains,
    FileWritten,
    MaxChars,
    _cited_numbers,
    run_checks,
)
from deputy.config import Policy
from deputy.sandbox import Sandbox


def run(checks: list, answer: str, box: Sandbox, policy: Policy | None = None):  # type: ignore[type-arg]
    return asyncio.run(run_checks(checks, answer, box, policy or Policy()))


def test_contains_is_case_insensitive_and_lists_what_is_missing(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    ok = run([Contains(values=["Cosign", "digest"])], "uses COSIGN and a digest", box)
    bad = run([Contains(values=["cosign", "agent_min"])], "only cosign here", box)
    assert ok[0].passed
    assert not bad[0].passed
    assert "agent_min" in bad[0].detail


def test_cites_lines_accepts_ranges_in_two_languages(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    check = CitesLines(min=10, max=20)
    assert run([check], "rule (lines 10-12), other (righe 15, 20)", box)[0].passed
    assert run([check], "see l. 14", box)[0].passed


def test_cites_lines_rejects_out_of_range_and_missing_citations(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    check = CitesLines(min=10, max=20)
    out = run([check], "see lines 9 and 21-22", box)[0]
    assert not out.passed
    assert "9" in out.detail
    assert not run([check], "no numbers here", box)[0].passed


def test_max_chars_protects_the_orchestrator(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    assert run([MaxChars(limit=10)], "short", box)[0].passed
    assert not run([MaxChars(limit=3)], "too long", box)[0].passed


def test_file_written_needs_the_worker_to_have_written_it(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    (tmp_path / "pre.txt").write_text("existing")
    assert not run([FileWritten(path="pre.txt")], "", box)[0].passed
    box.write_file("new.txt", "x")
    assert run([FileWritten(path="new.txt")], "", box)[0].passed


def test_command_runs_only_when_the_policy_allows_it(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    argv = [sys.executable, "-c", "print('hi')"]
    denied = run([Command(argv=argv)], "", box)[0]
    allowed = run([Command(argv=argv)], "", box, Policy(allowed_commands=[[sys.executable, "-c"]]))[
        0
    ]
    assert not denied.passed
    assert "not allowed" in denied.detail
    assert allowed.passed


def test_command_failure_and_timeout_are_reported(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    policy = Policy(allowed_commands=[[sys.executable, "-c"]], command_timeout_s=0.5)
    failing = run([Command(argv=[sys.executable, "-c", "raise SystemExit(3)"])], "", box, policy)[0]
    slow = run(
        [Command(argv=[sys.executable, "-c", "import time; time.sleep(5)"])], "", box, policy
    )[0]
    assert not failing.passed
    assert slow.detail == "timed out"


def test_excludes_fails_when_forbidden_text_appears(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    assert run([Excludes(values=["SECRET"])], "all fine", box)[0].passed
    leaked = run([Excludes(values=["secret"])], "the token is SECRET-1", box)[0]
    assert not leaked.passed
    assert "SECRET-1" not in leaked.detail  # the report must not repeat the leak


def test_file_contains_reads_the_file_inside_the_sandbox(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    (tmp_path / "out.txt").write_text("hello deputy")
    assert run([FileContains(path="out.txt", values=["hello"])], "", box)[0].passed
    assert not run([FileContains(path="out.txt", values=["bye"])], "", box)[0].passed
    assert not run([FileContains(path="../x", values=["a"])], "", box)[0].passed
    assert not run([FileContains(path="missing.txt", values=["a"])], "", box)[0].passed


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("`long.log:540` has it", [540]),
        ("see src/deputy/a.py:12-14 and b.md:3", [12, 14, 3]),
        ("at 12:09:00 the worker failed", []),
        ("open http://localhost:8080/x or https://example.com:443/y", []),
        ("lines 3, 5 and file.txt:7", [3, 5, 7]),
    ],
)
def test_citation_forms_are_recognised_without_false_positives(
    answer: str, expected: list[int]
) -> None:
    assert sorted(_cited_numbers(answer)) == sorted(expected)
