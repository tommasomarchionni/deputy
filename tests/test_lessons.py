# pyright: basic
import asyncio
import json
import time
from typing import get_args

import pytest
from hypothesis import given
from hypothesis import strategies as st

from deputy import cli, lessons, runs
from deputy.agent import run_task
from deputy.checks import CheckSpec, CitesLines, Contains, MaxChars, to_check
from deputy.config import Policy, state_dir
from deputy.lessons import Evidence, Lesson, LessonBook
from deputy.profiles import load_profiles
from deputy.sandbox import Sandbox
from helpers import message

NOW = 1_800_000_000.0
DAY = lessons.SECONDS_PER_DAY


def run(
    kind_results: list[tuple[str, bool]],
    *,
    source: str = "mcp",
    profile: str = "helper",
    age_days: float = 0.0,
) -> dict:  # type: ignore[type-arg]
    return {
        "event": "run", "ts": NOW - age_days * DAY, "profile": profile, "source": source,
        "checks": [{"name": name, "passed": ok} for name, ok in kind_results],
    }  # fmt: skip


def lesson(
    kind: str = "contains",
    *,
    rate: float = 0.5,
    profile: str = "helper",
    expires: float = NOW + DAY,
) -> Lesson:  # type: ignore[type-arg]
    return Lesson(
        id=f"{profile}:{kind}", profile=profile, check_kind=kind, text=lessons.CATALOGUE[kind],
        evidence=Evidence(runs=10, failures=int(10 * rate), window_days=30),
        created_at=NOW, expires_at=expires,
    )  # fmt: skip


def test_every_kind_of_check_has_a_reminder_in_the_catalogue() -> None:
    kinds = set(get_args(CheckSpec.model_fields["type"].annotation))
    assert kinds == set(lessons.CATALOGUE)


def test_a_failing_check_kind_becomes_a_lesson_with_its_evidence() -> None:
    events = [run([("contains", False)]) for _ in range(3)] + [run([("contains", True)])] * 3
    (found,) = lessons.derive(events, now=NOW)
    assert found.id == "helper:contains"
    assert found.text == lessons.CATALOGUE["contains"]
    assert (found.evidence.runs, found.evidence.failures) == (6, 3)
    assert found.expires_at == NOW + lessons.LIFETIME_DAYS * DAY


def test_no_lesson_without_enough_runs_or_enough_failures() -> None:
    few = [run([("contains", False)])] * (lessons.MIN_RUNS - 1)
    rare = [run([("contains", False)])] + [run([("contains", True)])] * 9
    assert lessons.derive(few, now=NOW) == []
    assert lessons.derive(rare, now=NOW) == []


def test_benchmark_runs_and_old_runs_never_teach() -> None:
    bench = [run([("contains", False)], source="bench") for _ in range(9)]
    old = [run([("contains", False)], age_days=60) for _ in range(9)]
    assert lessons.derive(bench + old, now=NOW) == []


def test_file_checks_are_grouped_by_kind_and_each_profile_is_judged_alone() -> None:
    events = [run([("file_written: a.txt", False)]) for _ in range(5)]
    events += [run([("file_written: b.txt", False)], profile="voice-dev") for _ in range(5)]
    assert [item.id for item in lessons.derive(events, now=NOW)] == [
        "helper:file_written",
        "voice-dev:file_written",
    ]


def test_merging_keeps_what_the_evidence_supports_and_what_the_user_muted_stays_muted() -> None:
    book = LessonBook(lessons=[lesson("max_chars")], muted=["helper:contains"])
    merged = lessons.merge(book, [lesson("contains"), lesson("cites_lines")])
    assert [item.id for item in merged.lessons] == [
        "helper:cites_lines"
    ]  # not max_chars: no longer true
    assert merged.muted == ["helper:contains"]


def test_only_relevant_active_lessons_apply_the_worst_first_and_at_most_three() -> None:
    book = LessonBook(
        lessons=[
            lesson("contains", rate=0.3),
            lesson("cites_lines", rate=0.9),
            lesson("max_chars", rate=0.5),
            lesson("excludes", rate=0.4),
            lesson("command", expires=NOW - 1),
            lesson("file_written", profile="other"),
        ]
    )
    checks = [Contains(values=["a"]), CitesLines(max=3), MaxChars(limit=9)]
    chosen = lessons.applicable("helper", checks, now=NOW, book=book)
    assert [item.check_kind for item in chosen] == ["cites_lines", "max_chars", "contains"]
    assert lessons.applicable("helper", [], now=NOW, book=book) == []  # no check, nothing relevant
    wide = [to_check(CheckSpec(type="command", argv=["x"])), *checks]
    assert "command" not in [
        i.check_kind for i in lessons.applicable("helper", wide, now=NOW, book=book)
    ]


@given(
    st.lists(st.sampled_from(sorted(lessons.CATALOGUE)), unique=True),
    st.lists(st.integers(-5, 5), min_size=1, max_size=8),
)
def test_applicable_lessons_are_bounded_and_never_expired(kinds, offsets) -> None:  # type: ignore[no-untyped-def]
    book = LessonBook(
        lessons=[
            lesson(kind, expires=NOW + offsets[i % len(offsets)] * DAY)
            for i, kind in enumerate(kinds)
        ]
    )
    checks = [
        to_check(CheckSpec(type=k, values=["a"], max=3, limit=9, path="p", argv=["x"]))
        for k in kinds
    ]
    chosen = lessons.applicable("helper", checks, now=NOW, book=book)
    assert len(chosen) <= lessons.MAX_APPLIED
    assert all(item.active(NOW) for item in chosen)


def test_the_prompt_gets_the_lessons_and_nothing_else_changes_without_them() -> None:
    assert lessons.prompt_addendum([]) == ""
    text = lessons.prompt_addendum([lesson("contains")])
    assert text.startswith("\n\nLessons from your past runs")
    assert lessons.CATALOGUE["contains"] in text


def test_a_run_puts_the_lessons_in_the_system_prompt_and_in_the_trace(
    fake_client, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    sent: list[str] = []
    client = fake_client([message("done")])
    original = client.chat

    async def spy(messages, tools=None):  # type: ignore[no-untyped-def]
        sent.append(messages[0]["content"])
        return await original(messages, tools)

    client.chat = spy  # type: ignore[method-assign]
    asyncio.run(
        run_task(
            client=client, profile=load_profiles()["helper"], task="x", sandbox=Sandbox(tmp_path),
            checks=[], policy=Policy(), lessons=[lesson("contains")],
        )
    )  # fmt: skip
    assert lessons.CATALOGUE["contains"] in sent[0]
    line = (state_dir() / "runs.jsonl").read_text().splitlines()[-1]
    assert json.loads(line)["lessons"] == ["helper:contains"]


def test_the_book_survives_a_round_trip_and_a_damaged_file_is_an_empty_book() -> None:
    assert lessons.load_book() == LessonBook()
    lessons.save_book(LessonBook(lessons=[lesson()], muted=["x:y"]))
    assert lessons.load_book().lessons[0].id == "helper:contains"
    (state_dir() / "lessons.json").write_text("{broken")
    assert lessons.load_book() == LessonBook()


def seed_failures(count: int) -> None:
    for _ in range(count):
        runs.record({"event": "run", "run_id": "r", "profile": "helper", "source": "mcp",
                     "status": "checks_failed", "checks": [{"name": "contains", "passed": False}],
                     "metrics": {}, "saved_est": 0})  # fmt: skip


def test_learn_reports_first_and_saves_only_with_apply(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["learn"]) == 0
    assert "nothing to learn yet" in capsys.readouterr().out
    seed_failures(6)
    assert cli.main(["learn"]) == 0
    out = capsys.readouterr().out
    assert "helper:contains: 6 of 6 runs failed this check (100%)" in out
    assert "report only" in out
    assert lessons.load_book().lessons == []
    assert cli.main(["learn", "--apply"]) == 0
    assert [item.id for item in lessons.load_book().lessons] == ["helper:contains"]


def test_lessons_can_be_listed_and_removed_for_good(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["lessons"]) == 0
    assert "no lessons" in capsys.readouterr().out
    seed_failures(6)
    cli.main(["learn", "--apply"])
    capsys.readouterr()
    assert cli.main(["lessons", "list"]) == 0
    assert "helper:contains" in capsys.readouterr().out
    assert cli.main(["lessons", "remove", "nope"]) == 1
    assert cli.main(["lessons", "remove", "helper:contains"]) == 0
    capsys.readouterr()
    cli.main(["learn", "--apply"])  # the evidence is still there, but the user said no
    assert lessons.load_book().lessons == []
    assert lessons.load_book().muted == ["helper:contains"]
    assert time.time() > 0
