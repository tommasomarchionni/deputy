# pyright: basic
import pytest

from release_plan import MIN_HOURS, THRESHOLD, Commit, decide, next_version


def c(subject: str, body: str = "") -> Commit:
    return Commit(subject, body)


def plan(commits, *, last="0.1.0", hours=1.0, green=True, event="workflow_run", force=False):
    return decide(
        commits=commits,
        last_version=last,
        hours_since_last=hours,
        ci_green=green,
        event=event,
        force=force,
    )


@pytest.mark.parametrize(
    ("subject", "body", "releasable"),
    [
        ("feat(cli): x (#1)", "", True),
        ("fix: y", "", True),
        ("perf(bench): z", "", True),
        ("docs: d", "", False),
        ("ci: c", "", False),
        ("chore(deps): bump", "", False),
        ("refactor!: big", "", True),
        ("chore: x", "BREAKING CHANGE: removes it", True),
        ("not a conventional commit", "", False),
    ],
)
def test_which_commits_are_releasable(subject: str, body: str, releasable: bool) -> None:
    assert c(subject, body).releasable is releasable


@pytest.mark.parametrize(
    ("last", "commits", "expected"),
    [
        ("0.1.0", [c("fix: a")], "0.1.1"),
        ("0.1.0", [c("fix: a"), c("feat: b")], "0.2.0"),
        ("0.1.5", [c("feat!: b")], "0.2.0"),
        ("1.2.3", [c("fix: a")], "1.2.4"),
        ("1.2.3", [c("feat: a")], "1.3.0"),
        ("1.2.3", [c("fix!: a")], "2.0.0"),
        ("0.0.0", [c("feat: first")], "0.1.0"),
    ],
)
def test_the_next_version_follows_the_commit_types(last, commits, expected) -> None:
    assert next_version(last, commits) == expected


def test_nothing_is_released_when_main_is_not_green_even_if_forced() -> None:
    assert not plan([c("fix: a")], green=False, force=True).release


def test_nothing_is_released_without_a_releasable_change_even_if_forced() -> None:
    decision = plan([c("docs: a"), c("ci: b")], force=True, hours=999)
    assert not decision.release
    assert "nothing to release" in decision.reason


def test_ten_releasable_changes_release_now_on_any_event() -> None:
    commits = [c(f"fix: {i}") for i in range(THRESHOLD)]
    assert plan(commits, event="workflow_run", hours=0.1).release
    assert not plan(commits[:-1], event="workflow_run", hours=0.1).release


def test_a_few_changes_wait_for_the_twelve_hour_window_on_the_schedule_only() -> None:
    few = [c("fix: a"), c("feat: b")]
    assert not plan(few, event="workflow_run", hours=MIN_HOURS + 5).release
    assert not plan(few, event="schedule", hours=MIN_HOURS - 0.5).release
    scheduled = plan(few, event="schedule", hours=MIN_HOURS)
    assert scheduled.release
    assert scheduled.version == "0.2.0"


def test_a_manual_run_can_force_a_release_inside_the_window() -> None:
    decision = plan([c("fix: a")], event="workflow_dispatch", hours=0.2, force=True)
    assert decision.release
    assert "forced" in decision.reason


def test_a_manual_run_without_force_follows_the_thresholds() -> None:
    assert not plan([c("fix: a")], event="workflow_dispatch", hours=100).release
