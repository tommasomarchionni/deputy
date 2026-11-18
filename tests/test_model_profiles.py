# pyright: basic
import json
import time

import pytest

from deputy.agent import Metrics, Result
from deputy.bench import Summary, summarise
from deputy.config import Backend
from deputy.model_profiles import (
    DEFAULT_MAX_TURNS,
    MAX_MAX_TURNS,
    MIN_MAX_TURNS,
    ModelProfile,
    changed_model,
    describe,
    load_profile,
    profile_path,
    run_budget,
    suggested_max_turns,
)
from deputy.tune import save_model_profile

BACKEND = Backend(name="local", base_url="http://x", model="default")


def summary(**kw: float | str) -> Summary:
    base = {
        "variant": "baseline", "runs": 5, "pass_rate": 0.8, "citation_rate": 1.0, "mean_turns": 2.5,
        "mean_tokens_out": 100, "mean_elapsed_s": 30, "stdev_elapsed_s": 5, "tokens_per_s": 10,
        "served_model": "qwen-27b",
    }  # fmt: skip
    return Summary(**{**base, **kw})


def profile(**kw) -> ModelProfile:  # type: ignore[no-untyped-def]
    base = {
        "backend": "local", "model": "default", "served_model": "qwen-27b",
        "measured_at": time.time(), "pass_rate": 1.0, "mean_turns": 2.0, "mean_elapsed_s": 20.0,
    }  # fmt: skip
    return ModelProfile(**{**base, **kw})


def test_a_saved_benchmark_can_be_read_back_with_the_model_that_answered() -> None:
    assert load_profile(BACKEND) is None
    save_model_profile(BACKEND, summary(), [])
    loaded = load_profile(BACKEND)
    assert loaded is not None
    assert (loaded.served_model, loaded.pass_rate, loaded.mean_turns) == ("qwen-27b", 0.8, 2.5)
    assert abs(loaded.measured_at - time.time()) < 5


def test_an_unreadable_or_incomplete_profile_is_ignored_not_fatal() -> None:
    path = profile_path(BACKEND)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")
    assert load_profile(BACKEND) is None
    path.write_text(json.dumps({"backend": "local", "model": "default"}))
    assert load_profile(BACKEND) is None


def test_an_older_profile_without_a_timestamp_uses_the_file_time() -> None:
    save_model_profile(BACKEND, summary(), [])
    path = profile_path(BACKEND)
    data = json.loads(path.read_text())
    del data["measured_at"], data["served_model"]
    path.write_text(json.dumps(data))
    loaded = load_profile(BACKEND)
    assert loaded is not None
    assert loaded.served_model == ""
    assert abs(loaded.measured_at - time.time()) < 5


@pytest.mark.parametrize(
    ("mean_turns", "expected"),
    [(0.0, DEFAULT_MAX_TURNS), (1.0, MIN_MAX_TURNS), (2.5, 5), (4.0, 8), (9.0, MAX_MAX_TURNS)],
)
def test_the_turn_limit_follows_the_measured_turns_within_bounds(
    mean_turns: float, expected: int
) -> None:
    assert suggested_max_turns(profile(mean_turns=mean_turns)) == expected


def test_without_a_profile_the_default_limit_applies() -> None:
    assert suggested_max_turns(None) == DEFAULT_MAX_TURNS


def test_status_text_says_when_it_was_measured_and_whether_the_model_changed() -> None:
    now = time.time()
    assert describe(None, ["x"]) == "never measured: run deputy tune"
    fresh = describe(profile(measured_at=now), ["qwen-27b"], now=now)
    assert fresh == "measured today: 100% passed, 2.0 turns"
    older = describe(profile(measured_at=now - 3 * 86_400), ["qwen-27b"], now=now)
    assert "3 days ago" in older
    changed = describe(profile(), ["llama-70b"], now=time.time())
    assert "now serves llama-70b, not qwen-27b" in changed
    assert "run deputy tune" in changed
    assert "serves" not in describe(profile(served_model=""), ["llama-70b"])  # nothing to compare
    assert "serves" not in describe(profile(), [])  # the server listed no models


def test_a_benchmark_summary_records_the_most_common_answering_model() -> None:
    def result(model: str) -> Result:
        return Result(run_id="r", status="ok", metrics=Metrics(model=model, elapsed_s=1))

    got = summarise(
        "v", [("a", result("m1")), ("b", result("m2")), ("c", result("m2")), ("d", result(""))]
    )
    assert got.served_model == "m2"
    assert summarise("v", []).served_model == ""


def test_changed_model_names_the_new_model_only_when_it_differs_from_the_measured_one() -> None:
    measured = ModelProfile("local", "default", "qwen-27b", time.time(), 1.0, 2.0, 10.0)
    assert changed_model(measured, ["qwen-27b"]) is None
    assert changed_model(measured, ["qwen-27b", "other"]) is None
    assert changed_model(measured, ["llama-70b"]) == "llama-70b"
    assert changed_model(measured, []) is None  # the server did not say
    assert changed_model(None, ["llama-70b"]) is None  # never measured: nothing changed
    unknown = ModelProfile("local", "default", "", time.time(), 1.0, 2.0, 10.0)
    assert changed_model(unknown, ["llama-70b"]) is None


def test_the_run_budget_grows_with_a_slow_measured_model_and_zero_disables_it() -> None:
    def measured(seconds: float) -> ModelProfile:
        return ModelProfile("b", "m", "m", 0.0, 1.0, 2.0, seconds)

    assert run_budget(900, None) == 900
    assert run_budget(900, measured(20)) == 900  # a fast model keeps the floor
    assert run_budget(900, measured(300)) == 6000  # a slow one gets 20 times its measured time
    assert run_budget(0, measured(300)) == 0
