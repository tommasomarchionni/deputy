# pyright: basic
import asyncio
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from deputy.checks import (
    CitesLines,
    CitesText,
    Command,
    Contains,
    Excludes,
    FileContains,
    FileWritten,
    MaxChars,
    cited_numbers,
    quoted_citations,
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
    assert sorted(cited_numbers(answer)) == sorted(expected)


FILE_TEXT = (
    "alpha one\nRetention of audit logs is 30 days\nthird   line  here\nlast line of the file\n"
)


@pytest.fixture
def cited(tmp_path: Path) -> Sandbox:
    (tmp_path / "doc.txt").write_text(FILE_TEXT)
    return Sandbox(tmp_path)


def cites_text(answer: str, box: Sandbox, path: str = "doc.txt"):  # type: ignore[no-untyped-def]
    return run([CitesText(path=path)], answer, box)[0]


def test_a_quote_that_is_on_the_cited_line_passes_in_every_citation_style(cited: Sandbox) -> None:
    for answer in (
        'line 2: "retention of audit logs is 30 days"',
        '2: "retention of audit logs"',
        "doc.txt:2 `audit logs is 30 days`",
        '"Retention of audit logs is 30 days" (line 2)',
        'righe 1-2: "alpha one" and "audit logs is 30 days"',
    ):
        assert cites_text(answer, cited).passed, answer


def test_a_quote_that_is_not_on_the_cited_line_fails_and_says_where(cited: Sandbox) -> None:
    result = cites_text('line 3: "retention of audit logs is 30 days"', cited)
    assert not result.passed
    assert "line 3 does not contain" in result.detail
    assert "retention of audit logs" in result.detail


def test_one_wrong_quote_among_right_ones_fails_the_check(cited: Sandbox) -> None:
    answer = 'line 1: "alpha one" and line 4: "retention of audit logs is 30 days"'
    result = cites_text(answer, cited)
    assert not result.passed
    assert "line 4" in result.detail
    assert "line 1" not in result.detail


def test_quotes_are_compared_without_regard_to_case_or_spacing(cited: Sandbox) -> None:
    assert cites_text('line 3: "THIRD line here"', cited).passed


def test_a_quote_beyond_the_end_of_the_file_fails(cited: Sandbox) -> None:
    assert not cites_text('line 99: "alpha one is here"', cited).passed


def test_an_answer_without_a_quoted_citation_fails_with_the_expected_form(cited: Sandbox) -> None:
    for answer in ("see line 2", 'it says "retention of audit logs" somewhere', 'line 2: "short"'):
        result = cites_text(answer, cited)
        assert not result.passed
        assert "no quoted citation" in result.detail


def test_an_unreadable_or_forbidden_path_fails_without_raising(cited: Sandbox) -> None:
    assert not cites_text('line 1: "alpha one is here"', cited, path="missing.txt").passed
    assert not cites_text('line 1: "alpha one is here"', cited, path="../x").passed


def test_apostrophes_in_prose_are_not_taken_for_quotes() -> None:
    assert quoted_citations("line 2: it's what they don't say, isn't it") == []


@given(
    st.lists(
        st.text(alphabet="abcdefghij klmnop", min_size=12, max_size=40), min_size=3, max_size=12
    ),
    st.data(),
)
def test_a_quote_copied_from_a_line_always_verifies_on_that_line_and_not_elsewhere(
    lines, data
) -> None:  # type: ignore[no-untyped-def]
    lines = [" ".join(line.split()) for line in lines]
    index = data.draw(st.integers(0, len(lines) - 1))
    quote = lines[index]
    if len(quote) < 8 or sum(quote in other for other in lines) != 1:
        return  # not an unambiguous quote: nothing to assert
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "f.txt").write_text("\n".join(lines))
        box = Sandbox(root)
        assert cites_text(f'line {index + 1}: "{quote}"', box, "f.txt").passed
        other = (index + 1) % len(lines) + 1
        assert not cites_text(f'line {other}: "{quote}"', box, "f.txt").passed
