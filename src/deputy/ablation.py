"""Does a lesson help? Run the benchmark tasks it can affect without it and with it, and compare.

A lesson stays only while the run with it is not worse than the run without it. The comparison is
made on the benchmark tasks that carry a check of the lesson's kind, with the same model, settings
and number of repetitions on both sides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from deputy import lessons
from deputy.bench import Summary, Variant, run_suite, tasks_with_kind
from deputy.config import Backend, Policy

Verdict = Literal["helps", "neutral", "hurts", "untestable"]


@dataclass(frozen=True)
class Ablation:
    lesson: lessons.Lesson
    tasks: list[str]
    without: Summary | None
    with_lesson: Summary | None
    verdict: Verdict


def judge(without: Summary, with_lesson: Summary) -> Verdict:
    """Hurts if the run with the lesson passes less or cites worse; helps if it passes more or
    cites better; otherwise the lesson changes nothing the benchmark can see."""
    if (
        with_lesson.pass_rate < without.pass_rate
        or with_lesson.citation_rate < without.citation_rate
    ):
        return "hurts"
    if (
        with_lesson.pass_rate > without.pass_rate
        or with_lesson.citation_rate > without.citation_rate
    ):
        return "helps"
    return "neutral"


async def ablate(
    backend: Backend,
    lesson: lessons.Lesson,
    *,
    runs: int,
    policy: Policy | None = None,
    run_code: bool = False,
) -> Ablation:
    """The benchmark without the lesson, then with only this lesson."""
    names = tasks_with_kind(lesson.check_kind, run_code=run_code)
    if not names:
        return Ablation(lesson, [], None, None, "untestable")
    book = lessons.LessonBook(lessons=[lesson])
    plain = await run_suite(
        backend, Variant(name="without"), runs=runs, only=names, run_code=run_code, policy=policy
    )
    taught = await run_suite(
        backend,
        Variant(name="with lesson"),
        runs=runs,
        only=names,
        run_code=run_code,
        lesson_book=book,
        policy=policy,
    )
    return Ablation(lesson, names, plain, taught, judge(plain, taught))
