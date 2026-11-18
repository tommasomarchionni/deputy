# pyright: basic
import asyncio
import json
import tomllib
from pathlib import Path

import pytest

from deputy import cli
from deputy.agent import Metrics, Result
from deputy.bench import (
    DECOY,
    HOLDOUT,
    NEEDLE_LINE,
    Summary,
    Variant,
    apply_variant,
    build_project,
    candidate_variants,
    choose_best,
    confirms,
    holdout_names,
    run_suite,
    selection_names,
    summarise,
    tasks,
)
from deputy.checks import CheckResult
from deputy.cli.tuning import format_table
from deputy.config import Backend, config_path, load_config
from deputy.tune import merge_tuned, read_tuned, revert_tuned, tuned_path, write_tuned
from helpers import message, tool_call


def result(
    status: str = "ok", elapsed: float = 10, out: int = 500, cites: bool | None = True
) -> Result:
    checks = [] if cites is None else [CheckResult(name="cites_lines", passed=cites)]
    return Result(
        run_id="r",
        status=status,  # type: ignore[arg-type]
        checks=checks,
        metrics=Metrics(turns=2, tokens_out=out, elapsed_s=elapsed),
    )


def summary(name: str, **kw: float) -> Summary:
    base = {
        "variant": name, "runs": 5, "pass_rate": 1.0, "citation_rate": 1.0, "mean_turns": 2,
        "mean_tokens_out": 1000, "mean_elapsed_s": 100, "stdev_elapsed_s": 10, "tokens_per_s": 10,
    }  # fmt: skip
    return Summary(**{**base, **kw})


def test_the_synthetic_project_is_deterministic_and_plants_the_traps(tmp_path: Path) -> None:
    build_project(tmp_path)
    log = (tmp_path / "long.log").read_text().splitlines()
    assert "CODE-7431" in log[NEEDLE_LINE - 1]
    assert DECOY in (tmp_path / ".env").read_text()
    again = tmp_path / "again"
    again.mkdir()
    build_project(again)
    assert (again / "policy.md").read_text() == (tmp_path / "policy.md").read_text()


def test_every_task_has_a_name_and_at_least_one_check() -> None:
    for task in tasks():
        assert task.name
        assert task.checks


def test_summarise_counts_passes_failures_and_speed() -> None:
    s = summarise(
        "v",
        [("a", result(elapsed=10, out=100)), ("a", result("checks_failed", 30, 300, cites=False))],
    )
    assert s.pass_rate == 0.5
    assert s.citation_rate == 0.5
    assert s.mean_elapsed_s == 20
    assert s.tokens_per_s == 10
    assert s.failures == {"a": 1}
    assert summarise("empty", []).runs == 0


def test_a_candidate_must_keep_accuracy_and_be_clearly_cheaper() -> None:
    base = summary("baseline")
    faster = summary("fast", mean_elapsed_s=60)
    leaner = summary("lean", mean_tokens_out=700, mean_elapsed_s=95)
    worse = summary("worse", pass_rate=0.8, mean_elapsed_s=10)
    noise = summary("noise", mean_elapsed_s=95, mean_tokens_out=950)
    assert choose_best(base, [worse, noise]) is None
    assert choose_best(base, [noise, leaner, faster, worse]).variant == "fast"  # type: ignore[union-attr]
    assert choose_best(base, [leaner]).variant == "lean"  # type: ignore[union-attr]


def test_a_variant_overlays_the_backend_without_mutating_it() -> None:
    backend = Backend(name="b", base_url="http://x", max_tokens=4096, extra_body={"a": 1})
    out = apply_variant(backend, Variant(name="v", extra_body={"b": 2}, max_tokens=512))
    assert out.extra_body == {"a": 1, "b": 2}
    assert out.max_tokens == 512
    assert backend.extra_body == {"a": 1}


def test_run_suite_against_a_scripted_model(fake_client) -> None:  # type: ignore[no-untyped-def]
    # one task, the write-file one: the model writes the file then says so
    script = [
        message(
            tool_calls=[tool_call("write_file", {"path": "out.txt", "content": "hello deputy"})]
        ),
        message("done"),
    ]
    seen: list[str] = []
    summary_ = asyncio.run(
        run_suite(
            Backend(name="b", base_url="http://x"),
            Variant(name="baseline"),
            runs=1,
            only=["write-file"],
            client_factory=lambda backend: fake_client(script),
            on_result=lambda name, r: seen.append(f"{name}:{r.status}"),
        )
    )
    assert seen == ["write-file:ok"]
    assert summary_.pass_rate == 1.0


def test_tuning_is_written_merged_and_reverted(tmp_path: Path) -> None:
    backend = Backend(name="local", base_url="http://x", extra_body={"top_k": 20})
    write_tuned(
        "local", Variant(name="v", extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    )
    data = tomllib.loads(tuned_path().read_text())
    assert (
        data["backends"]["local"]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    )
    merged = merge_tuned(backend, read_tuned())
    assert merged.extra_body["top_k"] == 20
    assert merged.extra_body["chat_template_kwargs"] == {"enable_thinking": False}
    write_tuned("local", Variant(name="w", max_tokens=777))
    assert merge_tuned(backend, read_tuned()).max_tokens == 777
    assert "restored" in revert_tuned()
    assert "chat_template_kwargs" in tuned_path().read_text()
    assert "removed" in revert_tuned()
    assert not tuned_path().exists()
    assert revert_tuned() == "nothing to revert"


def test_a_broken_tuned_file_is_ignored_not_fatal() -> None:
    tuned_path().parent.mkdir(parents=True, exist_ok=True)
    tuned_path().write_text("not [valid")
    assert read_tuned() == {}


def test_load_config_applies_tuning_over_the_users_settings() -> None:
    cli.main(["init", "--yes", "--url", "http://h:1", "--model", "m"])
    write_tuned("local", Variant(name="v", max_tokens=321))
    assert load_config().backend().max_tokens == 321


@pytest.mark.parametrize("mode", ["propose", "apply", "off"])
def test_tune_follows_the_auto_tune_setting(
    mode: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init", "--yes", "--url", "http://h:1", "--model", "m"])
    cfg = config_path()
    cfg.write_text(f'auto_tune = "{mode}"\n' + cfg.read_text())
    wins = {
        "baseline": summary("baseline"),
        "thinking-off": summary("thinking-off", mean_elapsed_s=40),
    }

    async def fake_suite(backend, variant, **kw):  # type: ignore[no-untyped-def]
        return wins.get(variant.name, summary(variant.name, pass_rate=0.1))

    monkeypatch.setattr("deputy.cli.tuning.run_suite", fake_suite)
    assert cli.main(["tune"]) == 0
    out = capsys.readouterr().out
    assert "recommendation: thinking-off" in out
    assert tuned_path().exists() == (mode == "apply")
    if mode == "propose":
        assert "deputy tune --apply" in out
    if mode == "off":
        assert "nothing changed" in out


def test_tune_apply_flag_and_revert_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init", "--yes", "--url", "http://h:1", "--model", "m"])

    async def fake_suite(backend, variant, **kw):  # type: ignore[no-untyped-def]
        return summary(variant.name, mean_elapsed_s=40 if variant.name == "short-output" else 100)

    monkeypatch.setattr("deputy.cli.tuning.run_suite", fake_suite)
    assert cli.main(["tune", "--apply"]) == 0
    assert "applied" in capsys.readouterr().out
    assert tuned_path().exists()
    assert cli.main(["tune", "--revert"]) == 0
    assert not tuned_path().exists()


def test_tune_keeps_settings_when_nothing_is_better_and_hints_at_the_server(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init", "--yes", "--url", "http://h:1", "--model", "m"])

    async def fake_suite(backend, variant, **kw):  # type: ignore[no-untyped-def]
        return summary(variant.name, mean_tokens_out=1500)

    monkeypatch.setattr("deputy.cli.tuning.run_suite", fake_suite)
    assert cli.main(["tune"]) == 0
    out = capsys.readouterr().out
    assert "keep your current settings" in out
    assert "--reasoning-budget" in out


def test_bench_prints_a_table_and_saves_the_profile(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init", "--yes", "--url", "http://h:1", "--model", "my/model:Q4"])

    async def fake_suite(backend, variant, **kw):  # type: ignore[no-untyped-def]
        return summary(variant.name)

    monkeypatch.setattr("deputy.cli.tuning.run_suite", fake_suite)
    assert cli.main(["bench", "--variant", 'cool={"extra_body":{"temperature":0.1}}']) == 0
    out = capsys.readouterr().out
    assert "baseline" in out
    assert "cool" in out
    assert "profile saved" in out
    assert cli.main(["bench", "--variant", "oops"]) == 1


def test_the_saving_for_the_orchestrator_is_summarised_and_shown() -> None:
    cheap = result()
    cheap.orchestrator_tokens_saved_est = 300
    big = result()
    big.orchestrator_tokens_saved_est = 900
    s = summarise("v", [("a", cheap), ("b", big)])
    assert s.mean_saved_tokens_est == 600
    assert "saved*" in format_table([s])


def test_bench_json_prints_machine_readable_summaries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init", "--yes", "--url", "http://h:1", "--model", "m"])
    capsys.readouterr()  # drop what init printed, so only the JSON is left

    async def fake_suite(backend, variant, **kw):  # type: ignore[no-untyped-def]
        return summary(variant.name)

    monkeypatch.setattr("deputy.cli.tuning.run_suite", fake_suite)
    assert cli.main(["bench", "--json", "--variant", 'cool={"max_tokens":512}']) == 0
    data = json.loads(capsys.readouterr().out)
    assert [s["variant"] for s in data["summaries"]] == ["baseline", "cool"]
    assert data["summaries"][0]["pass_rate"] == 1.0
    assert data["profile"].endswith(".json")


def test_the_spread_of_every_metric_is_reported_across_repetitions() -> None:
    results = [("a", result(out=100, elapsed=10)), ("a", result(out=300, elapsed=30))]
    got = summarise("v", results, rep_pass_rates=[1.0, 0.5])
    assert got.pass_rate_stdev == 0.25
    assert got.stdev_tokens_out == 100.0
    assert got.stdev_elapsed_s == 10.0
    assert got.stdev_turns == 0.0
    assert summarise("v", results).pass_rate_stdev == 0.0  # one repetition: no spread to speak of


def test_repeating_the_suite_shows_the_pass_rate_varying_between_repetitions(fake_client) -> None:  # type: ignore[no-untyped-def]
    replies = [message("short"), message("x" * 500), message("short")]  # pass, fail, pass
    scripted = iter(replies)
    got = asyncio.run(
        run_suite(
            Backend(name="b", base_url="http://x"),
            Variant(name="v"),
            runs=3,
            only=["short-summary"],
            client_factory=lambda backend: fake_client([next(scripted)]),
        )
    )
    assert got.runs == 3
    assert got.pass_rate == pytest.approx(2 / 3, abs=0.001)
    assert got.pass_rate_stdev == pytest.approx(0.471, abs=0.001)  # per repetition: 1, 0, 1
    assert "±47" in format_table([got])


def test_the_held_out_tasks_are_fixed_apart_from_the_choice_and_never_run_code() -> None:
    assert holdout_names()
    assert selection_names()
    assert set(holdout_names()) == set(HOLDOUT)
    assert not set(selection_names()) & set(holdout_names())
    assert "write-passing-test" not in selection_names() + holdout_names()


def test_a_winner_holds_up_only_if_it_loses_no_accuracy_on_the_held_out_tasks() -> None:
    base = summary("baseline")
    assert confirms(base, summary("w"))
    assert not confirms(base, summary("w", pass_rate=0.9))
    assert not confirms(base, summary("w", citation_rate=0.5))


def tune_with(
    monkeypatch, calls: list[tuple[str, list[str] | None]], held_winner_pass: float = 1.0
):  # type: ignore[no-untyped-def]
    """A scripted suite: the 'thinking-off' candidate is faster on the choice tasks; on the
    held-out tasks it scores `held_winner_pass`."""

    async def fake_suite(backend, variant, **kw):  # type: ignore[no-untyped-def]
        calls.append((variant.name, kw.get("only")))
        if kw.get("only") == holdout_names() and variant.name == "thinking-off":
            return summary(variant.name, pass_rate=held_winner_pass)
        return summary(variant.name, mean_elapsed_s=40 if variant.name == "thinking-off" else 100)

    monkeypatch.setattr("deputy.cli.tuning.run_suite", fake_suite)


def test_tune_chooses_on_the_selection_tasks_and_confirms_on_the_held_out_ones(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init", "--yes", "--url", "http://h:1", "--model", "m"])
    calls: list[tuple[str, list[str] | None]] = []
    tune_with(monkeypatch, calls)
    assert cli.main(["tune"]) == 0
    out = capsys.readouterr().out
    choosing = [c for c in calls if c[1] == selection_names()]
    confirming = [c for c in calls if c[1] == holdout_names()]
    assert choosing[0][0] == "baseline"
    assert len(choosing) == 1 + len(candidate_variants())
    assert [name for name, _ in confirming] == ["baseline", "thinking-off"]
    assert "held-out confirmation" in out
    assert "recommendation: thinking-off" in out
    assert "confirmed on held-out tasks" in out


def test_a_recommendation_that_fails_on_the_held_out_tasks_is_withdrawn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init", "--yes", "--url", "http://h:1", "--model", "m"])
    tune_with(monkeypatch, [], held_winner_pass=0.5)
    assert cli.main(["tune", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "recommendation withdrawn" in out
    assert not tuned_path().exists()  # nothing applied


def test_narrowing_with_tasks_keeps_the_held_out_ones_out_or_refuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init", "--yes", "--url", "http://h:1", "--model", "m"])
    tune_with(monkeypatch, [])
    assert cli.main(["tune", "--tasks", "cited-extract,short-summary"]) == 0
    assert "not confirmed on held-out tasks" in capsys.readouterr().out
    assert (
        cli.main(["tune", "--tasks", "needle-in-long-file"]) == 1
    )  # only held-out left: nothing to choose on
    assert "no tuning task left" in capsys.readouterr().err
