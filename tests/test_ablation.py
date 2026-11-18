# pyright: basic
import asyncio
import time

import pytest

from deputy import ablation, cli, lessons
from deputy.bench import Summary, Variant, run_suite, tasks, tasks_with_kind
from deputy.config import Backend
from deputy.lessons import Evidence, Lesson, LessonBook
from helpers import message


def summary(name: str = "v", **kw: float) -> Summary:
    base = {
        "variant": name, "runs": 4, "pass_rate": 0.75, "citation_rate": 1.0, "mean_turns": 2.0,
        "mean_tokens_out": 100, "mean_elapsed_s": 10, "stdev_elapsed_s": 1, "tokens_per_s": 10,
    }  # fmt: skip
    return Summary(**{**base, **kw})


def lesson(kind: str = "contains", profile: str = "helper") -> Lesson:
    return Lesson(
        id=f"{profile}:{kind}", profile=profile, check_kind=kind, text=lessons.CATALOGUE[kind],
        evidence=Evidence(runs=10, failures=5, window_days=30),
        created_at=time.time(), expires_at=time.time() + 86_400,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("with_lesson", "verdict"),
    [
        (summary(pass_rate=0.5), "hurts"),
        (summary(citation_rate=0.5), "hurts"),
        (summary(pass_rate=1.0), "helps"),
        (summary(), "neutral"),
        (summary(pass_rate=1.0, citation_rate=0.5), "hurts"),  # a loss anywhere is a loss
    ],
)
def test_the_verdict_follows_accuracy_and_a_loss_anywhere_is_a_loss(
    with_lesson: Summary, verdict: str
) -> None:
    assert ablation.judge(summary(), with_lesson) == verdict


def test_the_tasks_a_lesson_can_affect_are_those_with_a_check_of_its_kind() -> None:
    assert "short-summary" in tasks_with_kind("max_chars")
    assert "cited-extract" in tasks_with_kind("cites_lines")
    assert tasks_with_kind("json_schema") == []
    assert all(
        t.name != "write-passing-test" for t in tasks() if t.name in tasks_with_kind("file_written")
    )
    assert "write-passing-test" in tasks_with_kind("command", run_code=True)
    assert tasks_with_kind("command") == []  # the code task is opt-in


def test_the_ablation_runs_the_same_tasks_without_then_with_only_this_lesson(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, list[str], list[str]]] = []

    async def fake_suite(backend, variant, **kw):  # type: ignore[no-untyped-def]
        book = kw.get("lesson_book")
        calls.append((variant.name, kw["only"], [x.id for x in book.lessons] if book else []))
        return summary(variant.name, pass_rate=1.0 if book else 0.5)

    monkeypatch.setattr(ablation, "run_suite", fake_suite)
    result = asyncio.run(
        ablation.ablate(Backend(name="b", base_url="http://x"), lesson("max_chars"), runs=3)
    )
    assert [c[0] for c in calls] == ["without", "with lesson"]
    assert calls[0][1] == calls[1][1] == tasks_with_kind("max_chars")
    assert calls[0][2] == []
    assert calls[1][2] == ["helper:max_chars"]
    assert result.verdict == "helps"


def test_a_lesson_no_benchmark_task_can_test_is_untestable_and_kept(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def never(*a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError("nothing to run")

    monkeypatch.setattr(ablation, "run_suite", never)
    result = asyncio.run(
        ablation.ablate(Backend(name="b", base_url="http://x"), lesson("json_schema"), runs=1)
    )
    assert result.verdict == "untestable"
    assert result.without is None


def test_a_benchmark_run_applies_exactly_the_lessons_it_is_given(fake_client) -> None:  # type: ignore[no-untyped-def]
    prompts: list[str] = []

    def spying(replies):  # type: ignore[no-untyped-def]
        client = fake_client(replies)
        original = client.chat

        async def spy(messages, tools=None):  # type: ignore[no-untyped-def]
            prompts.append(messages[0]["content"])
            return await original(messages, tools)

        client.chat = spy  # type: ignore[method-assign]
        return client

    def go(**kw):  # type: ignore[no-untyped-def]
        return asyncio.run(
            run_suite(
                Backend(name="b", base_url="http://x"), Variant(name="v"), runs=1,
                only=["short-summary"], client_factory=lambda b: spying([message("short")]), **kw,
            )
        )  # fmt: skip

    go()
    go(lesson_book=LessonBook(lessons=[lesson("max_chars")]))
    go(lesson_book=LessonBook(lessons=[lesson("contains")]))  # not relevant to this task
    assert lessons.CATALOGUE["max_chars"] not in prompts[0]
    assert lessons.CATALOGUE["max_chars"] in prompts[1]
    assert lessons.CATALOGUE["max_chars"] not in prompts[2]
    assert lessons.CATALOGUE["contains"] not in prompts[2]


def configure_with_lessons(*kinds: str) -> None:
    cli.main(["init", "--yes", "--url", "http://h:1", "--model", "m"])
    lessons.save_book(LessonBook(lessons=[lesson(kind) for kind in kinds]))


def scripted_verdicts(monkeypatch, outcomes: dict[str, tuple[float, float]]) -> None:  # type: ignore[no-untyped-def]
    """outcomes: check kind -> (pass rate without, pass rate with)."""

    async def fake_suite(backend, variant, **kw):  # type: ignore[no-untyped-def]
        book = kw.get("lesson_book")
        kind = next(k for k in outcomes if kw["only"] == tasks_with_kind(k))
        return summary(variant.name, pass_rate=outcomes[kind][1 if book else 0])

    monkeypatch.setattr(ablation, "run_suite", fake_suite)


def test_check_reports_every_verdict_and_removes_only_what_hurts_with_apply(  # type: ignore[no-untyped-def]
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_with_lessons("contains", "max_chars", "cites_lines", "json_schema")
    scripted_verdicts(
        monkeypatch,
        {"contains": (0.5, 1.0), "max_chars": (1.0, 0.5), "cites_lines": (0.75, 0.75)},
    )
    assert cli.main(["lessons", "check"]) == 0
    out = capsys.readouterr().out
    assert "helper:contains: keep: it improved the benchmark" in out
    assert "helper:max_chars: DROP" in out
    assert "helper:cites_lines: kept: no effect" in out
    assert "helper:json_schema: kept: no benchmark task" in out
    assert "pass 100% -> 50%" in out
    assert "report only" in out
    assert len(lessons.load_book().lessons) == 4  # nothing changed without --apply

    assert cli.main(["lessons", "check", "--apply"]) == 0
    assert "removed and muted: helper:max_chars" in capsys.readouterr().out
    book = lessons.load_book()
    assert {item.id for item in book.lessons} == {
        "helper:contains",
        "helper:cites_lines",
        "helper:json_schema",
    }
    assert book.muted == ["helper:max_chars"]


def test_check_with_nothing_that_hurts_changes_nothing(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    configure_with_lessons("contains")
    scripted_verdicts(monkeypatch, {"contains": (0.5, 0.75)})
    assert cli.main(["lessons", "check", "--apply"]) == 0
    assert "nothing to remove" in capsys.readouterr().out
    assert len(lessons.load_book().lessons) == 1


def test_check_without_lessons_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["init", "--yes", "--url", "http://h:1", "--model", "m"])
    assert cli.main(["lessons", "check"]) == 0
    assert "no active lessons to check" in capsys.readouterr().out


def test_check_without_a_configuration_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    lessons.save_book(LessonBook(lessons=[lesson("contains")]))
    assert cli.main(["lessons", "check"]) == 1
    assert "deputy init" in capsys.readouterr().err
