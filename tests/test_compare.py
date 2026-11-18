# pyright: basic
import json
from pathlib import Path

import pytest

from deputy import cli, compare
from deputy.bench import Summary
from deputy.config import ConfigError


def summary(**over) -> Summary:  # type: ignore[no-untyped-def]
    base = {
        "variant": "baseline",
        "runs": 24,
        "pass_rate": 0.917,
        "citation_rate": 0.8,
        "mean_turns": 2.0,
        "mean_tokens_out": 300.0,
        "mean_elapsed_s": 25.0,
        "stdev_elapsed_s": 1.0,
        "tokens_per_s": 13.0,
        "mean_saved_tokens_est": 45.0,
        "served_model": "m1",
        "failures": {"needle": 1},
    }
    return Summary(**{**base, **over})


def report(tmp_path: Path, name: str, **over) -> Path:  # type: ignore[no-untyped-def]
    path = tmp_path / name
    path.write_text(json.dumps({"summaries": [summary(**over).model_dump()], "profile": "p"}))
    return path


def test_identical_reports_show_no_regression() -> None:
    out = compare.compare(summary(), summary())
    assert not out.regressed
    assert out.text.endswith("no regression")


def test_a_task_that_fails_more_is_named_but_alone_it_is_not_a_regression() -> None:
    out = compare.compare(summary(), summary(failures={"needle": 2, "summary": 1}))
    assert not out.regressed
    assert "failed more (change, not alone a regression): needle" in out.text
    assert "failed more (change, not alone a regression): summary" in out.text


def test_one_task_worse_and_another_better_at_the_same_pass_rate_is_no_regression() -> None:
    out = compare.compare(summary(failures={"a": 1}), summary(failures={"b": 1}))
    assert not out.regressed


def test_a_task_that_fails_less_is_reported_and_is_not_a_regression() -> None:
    out = compare.compare(summary(), summary(failures={}, pass_rate=0.97))
    assert not out.regressed
    assert "failed less: needle" in out.text


def test_a_big_drop_in_pass_rate_is_a_regression_even_without_a_new_failing_task() -> None:
    assert compare.compare(summary(), summary(pass_rate=0.7)).regressed


def test_a_changed_served_model_is_called_out() -> None:
    assert "model changed: m1 -> m2" in compare.compare(summary(), summary(served_model="m2")).text


def test_a_file_that_is_not_a_report_is_a_clear_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    with pytest.raises(ConfigError):
        compare.load_baseline(bad)


def test_the_command_exits_zero_by_default_and_one_only_with_the_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a = report(tmp_path, "a.json")
    b = report(tmp_path, "b.json", failures={"needle": 3}, pass_rate=0.6)
    assert cli.main(["bench", "--compare", str(a), str(b)]) == 0
    assert "REGRESSION" in capsys.readouterr().out
    assert cli.main(["bench", "--compare", str(a), str(b), "--fail-on-regression"]) == 1
    assert cli.main(["bench", "--compare", str(a), str(tmp_path / "missing.json")]) == 1
