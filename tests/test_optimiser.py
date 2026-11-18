# pyright: basic
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from deputy import bench, cli, optimiser
from deputy.agent import Result
from deputy.backends import ChatClient
from deputy.bench import DECOY, Summary
from deputy.checks import CheckResult
from deputy.config import Backend, config_dir
from deputy.profiles import Profile, load_profiles, parse_profile
from helpers import message

PAD = " Keep every answer short, exact and in English; never read secrets." * 4
FIX_BODY = "BETTER-RULES: state both retention periods." + PAD
LEAN_BODY = "LEAN-RULES: be brief." + PAD


def summary(variant: str = "x", **kw: Any) -> Summary:
    base = {
        "runs": 4, "pass_rate": 0.5, "citation_rate": 1.0, "mean_turns": 2.0,
        "mean_tokens_out": 100.0, "mean_elapsed_s": 20.0, "stdev_elapsed_s": 2.0,
        "tokens_per_s": 5.0,
    }  # fmt: skip
    return Summary(variant=variant, **(base | kw))


class Lab:
    """A scripted model server. A profile whose body holds BETTER-RULES answers the retention task
    correctly; `leak` makes that same profile leak a secret on the held-out task."""

    def __init__(
        self, *, fix: str | None = FIX_BODY, lean: str | None = LEAN_BODY, leak: bool = False
    ):
        self.fix, self.lean, self.leak = fix, lean, leak
        self.reflections: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        first = body["messages"][0]["content"]
        if "tools" not in body:  # a reflection request
            self.reflections.append(first)
            reply = self.fix if "smallest edit" in first else self.lean
            return httpx.Response(200, json=message(reply or ""))
        task = body["messages"][-1]["content"]
        better = "BETTER-RULES" in first
        if task.startswith("Summarise the retention rules"):
            return httpx.Response(200, json=message("30 days and 10 years" if better else "unsure"))
        if task.startswith("Read the file .env") and better and self.leak:
            return httpx.Response(200, json=message(f"the token is {DECOY}"))
        return httpx.Response(200, json=message("fine"))

    def factory(self, backend: Backend) -> ChatClient:
        return ChatClient(backend, httpx.AsyncClient(transport=httpx.MockTransport(self.handler)))


@pytest.fixture(autouse=True)
def small_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench, "selection_names", lambda: ["short-with-facts", "short-summary"])
    monkeypatch.setattr(bench, "holdout_names", lambda: ["refuse-secret", "short-summary"])


def helper() -> Profile:
    return load_profiles()["helper"]


def run(lab: Lab) -> tuple[optimiser.Report, optimiser.Proposal | None]:
    return asyncio.run(
        optimiser.optimise(Backend(name="b", base_url="http://x"), helper(), factory=lab.factory)
    )


def test_a_better_profile_is_proposed_confirmed_and_not_applied(tmp_path: Path) -> None:
    lab = Lab()
    report, winner = run(lab)
    assert report.baseline.pass_rate == 0.5  # the retention task fails with the current profile
    assert winner is not None
    assert (report.winner, report.confirmed) == ("fix", True)
    assert [s.variant for s in report.candidates] == ["fix", "lean"]
    assert "BETTER-RULES" in report.diff
    folder = optimiser.save(report, helper(), winner)
    assert "is confirmed" in (folder / "report.md").read_text()
    assert "BETTER-RULES" in (folder / "candidate.md").read_text()
    assert not (
        config_dir() / "profiles" / "helper.md"
    ).exists()  # nothing applied without approval


def test_the_reflection_sees_what_failed(tmp_path: Path) -> None:
    lab = Lab()
    run(lab)
    fix = next(p for p in lab.reflections if "smallest edit" in p)
    assert "Summarise the retention rules" in fix  # the failing task
    assert "missing" in fix  # why it failed
    assert helper().body[:60] in fix  # and the current instructions


def test_a_proposal_that_loses_on_the_held_out_tasks_is_never_kept(tmp_path: Path) -> None:
    report, winner = run(Lab(leak=True))
    assert report.winner == "fix"  # it won on the selection tasks...
    assert (report.confirmed, winner) == (False, None)  # ...and did not hold up on the others
    folder = optimiser.save(report, helper(), winner)
    assert "did not hold up" in (folder / "report.md").read_text()
    assert not (folder / "candidate.md").exists()
    with pytest.raises(optimiser.OptimiserError, match="not confirmed"):
        optimiser.apply(folder.name)


def test_nothing_is_proposed_when_the_replies_are_unusable_or_not_better() -> None:
    report, winner = run(Lab(fix="too short", lean=helper().body))
    assert (report.candidates, report.winner, winner) == ([], None, None)
    assert "keep the current profile" in optimiser.to_markdown(report)


def test_applying_installs_the_profile_and_reverting_restores_the_previous_one() -> None:
    report, winner = run(Lab())
    folder = optimiser.save(report, helper(), winner)
    target = config_dir() / "profiles" / "helper.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\nname: helper\ndescription: mine\n---\n\nmy own words\n")
    optimiser.apply(folder.name)
    assert "BETTER-RULES" in target.read_text()
    assert parse_profile(target.read_text(), "user", "helper").name == "helper"
    with pytest.raises(optimiser.OptimiserError, match="already applied"):
        optimiser.apply(folder.name)
    optimiser.revert(folder.name)
    assert "my own words" in target.read_text()
    with pytest.raises(optimiser.OptimiserError, match="not applied"):
        optimiser.revert(folder.name)


def test_reverting_removes_a_profile_that_did_not_exist_before() -> None:
    report, winner = run(Lab())
    folder = optimiser.save(report, helper(), winner)
    target = optimiser.apply(folder.name)
    assert target.is_file()
    optimiser.revert(folder.name)
    assert not target.exists()


@pytest.mark.parametrize("bad", ["../etc", "nope", "", "2026/../x"])
def test_an_unknown_or_malicious_proposal_id_is_refused(bad: str) -> None:
    with pytest.raises(optimiser.OptimiserError, match="no proposal"):
        optimiser.apply(bad)


def test_a_reply_is_cleaned_and_bounded() -> None:
    body = "x" * 300
    assert optimiser.clean_body(f"```text\n{body}\n```") == body
    assert optimiser.clean_body("short") is None
    assert optimiser.clean_body("y" * (optimiser.MAX_BODY_CHARS + 1)) is None


def test_the_reflection_shows_a_bounded_number_of_failures() -> None:
    failures = [optimiser.Failure(f"t{i}", f"prompt {i}", "problem", "answer") for i in range(10)]
    text = optimiser.reflection_prompt(helper(), "fix", failures)
    assert text.count("what failed") == optimiser.MAX_FAILURES_SHOWN
    assert "nothing failed" in optimiser.reflection_prompt(helper(), "lean", [])


def test_a_failure_carries_the_task_the_failed_checks_and_a_short_answer() -> None:
    result = Result(
        run_id="r",
        status="checks_failed",
        answer="z" * 1000,
        checks=[CheckResult(name="contains", passed=False, detail="missing: ['30 days']")],
    )
    failure = optimiser.failure_from("short-with-facts", result)
    assert "retention" in failure.prompt
    assert failure.problems == "contains: missing: ['30 days']"
    assert len(failure.answer) == optimiser.ANSWER_EXCERPT_CHARS


def test_a_candidate_must_not_lose_accuracy_or_citations_and_must_gain() -> None:
    base = summary("current")
    assert optimiser.better(base, summary("a", pass_rate=0.75))
    assert not optimiser.better(base, summary("b", pass_rate=0.75, citation_rate=0.5))
    assert not optimiser.better(base, summary("c", pass_rate=0.25))
    assert not optimiser.better(base, summary("d"))  # same, no cheaper
    assert optimiser.better(base, summary("e", mean_tokens_out=60.0))  # same accuracy, leaner
    slow = summary("slow", pass_rate=1.0, mean_elapsed_s=30.0)
    fast = summary("fast", pass_rate=1.0, mean_elapsed_s=10.0)
    winner = optimiser.pick(base, [slow, fast])
    assert winner is not None
    assert winner.variant == "fast"
    assert optimiser.pick(base, []) is None


@given(
    name=st.from_regex(r"[a-z][a-z0-9-]{0,15}", fullmatch=True),
    description=st.from_regex(r"[A-Za-z][A-Za-z ,.]{0,40}[A-Za-z]", fullmatch=True),
    turns=st.none() | st.integers(1, 50),
    retries=st.none() | st.integers(0, 5),
)
def test_a_rendered_profile_keeps_its_front_matter_and_reads_back(
    name: str, description: str, turns: int | None, retries: int | None
) -> None:
    original = Profile(name, description, "old", "user", turns, retries)
    again = parse_profile(optimiser.render_profile(original, "new body"), "user", "x")
    assert (again.name, again.description) == (name, description)
    assert (again.max_turns, again.max_retries) == (turns, retries)
    assert again.body == "new body"
    assert again.problems == ()


def test_the_command_lists_applies_and_reports_errors(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["optimise", "--list"]) == 0
    assert "no proposals yet" in capsys.readouterr().out
    report, winner = run(Lab())
    folder = optimiser.save(report, helper(), winner)
    assert cli.main(["optimise", "--list"]) == 0
    assert "helper  confirmed  proposed" in capsys.readouterr().out
    assert cli.main(["optimise", "--apply", folder.name]) == 0
    assert "undo with: deputy optimise --revert" in capsys.readouterr().out
    assert cli.main(["optimise", "--revert", folder.name]) == 0
    assert cli.main(["optimise", "--apply", "nope"]) == 1
    assert "no proposal" in capsys.readouterr().err
