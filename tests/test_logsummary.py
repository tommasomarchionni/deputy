# pyright: basic
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from deputy import logsummary, tokens
from deputy.bench import HUGE_ERRORS, huge_log
from deputy.sandbox import Sandbox


def test_a_huge_log_becomes_a_few_lines_that_still_answer_who_failed_most() -> None:
    lines = huge_log().splitlines()
    out = logsummary.summarise(lines)
    assert tokens.estimate(out) < tokens.estimate(huge_log()) / 100
    assert "ERROR worker-#" in out
    for worker, count in HUGE_ERRORS.items():
        assert f"{worker.rsplit('-', 1)[1]}x{count}" in out


def test_a_line_that_occurs_once_is_shown_whole_with_its_line_number() -> None:
    lines = ["INFO ok 1"] * 50 + ["FATAL disk full on /dev/sda1"] + ["INFO ok 2"] * 50
    out = logsummary.summarise(lines)
    assert "51: FATAL disk full on /dev/sda1" in out


def test_repeated_lines_report_count_and_line_range() -> None:
    out = logsummary.summarise(["tick 1"] * 10 + ["tock"] + ["tick 7"] * 10)
    assert "1-21 x20: tick #1" in out
    assert "11: tock" in out


def test_a_varying_number_with_few_values_is_itemised() -> None:
    lines = [f"job {n % 3} done" for n in range(30)]
    assert "#1: 0x10, 1x10, 2x10" in logsummary.summarise(lines)


def test_an_empty_file_says_so() -> None:
    assert logsummary.summarise([]) == "[empty file]"


def test_a_very_long_line_is_cut() -> None:
    assert "x" * 300 not in logsummary.summarise(["x" * 5_000])


@settings(max_examples=60, deadline=None)
@given(st.lists(st.text(alphabet="ab 12:-", max_size=30), min_size=1, max_size=80))
def test_no_kind_of_line_is_ever_dropped(lines: list[str]) -> None:
    out = logsummary.summarise(lines)
    for line in lines:
        template = logsummary._template(line[: logsummary.MAX_LINE_CHARS])
        assert template in out or line[: logsummary.MAX_LINE_CHARS] in out


def test_the_sandbox_tool_counts_the_whole_file_as_read(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    (tmp_path / "app.log").write_text("\n".join(f"INFO batch {i} ok" for i in range(1_000)))
    out = box.summarise_log("app.log")
    assert "x1000" in out
    assert box.chars_read > 10_000
    assert "app.log" in box.files_read
