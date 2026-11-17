# pyright: basic
import asyncio
import sys
from pathlib import Path

from deputy.checks import CitesLines, Command, Contains, FileWritten, MaxChars, run_checks
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
