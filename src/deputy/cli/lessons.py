"""`deputy learn` derives lessons from real use; `deputy lessons` lists and mutes them."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from deputy import ablation, lessons, stats
from deputy.cli._common import Subparsers
from deputy.config import ConfigError, load_config


def _describe(lesson: lessons.Lesson, now: float) -> str:
    left = max(0.0, (lesson.expires_at - now) / lessons.SECONDS_PER_DAY)
    ev = lesson.evidence
    return (
        f"{lesson.id}: {ev.failures} of {ev.runs} runs failed this check ({ev.rate:.0%}), "
        f"expires in {left:.0f} days\n    {lesson.text}"
    )


def learn(args: argparse.Namespace) -> int:
    events, _ = stats.read_trace()
    derived = lessons.derive(events, days=args.days)
    book = lessons.load_book()
    now = time.time()
    proposed = lessons.merge(book, derived)
    if not proposed.lessons:
        print(
            f"nothing to learn yet: a lesson needs at least {lessons.MIN_RUNS} real runs with the "
            f"same check and a failure rate of {lessons.MIN_FAILURE_RATE:.0%} or more"
        )
    for lesson in proposed.lessons:
        print(_describe(lesson, now))
    if args.apply:
        lessons.save_book(proposed)
        print(f"\nsaved {len(proposed.lessons)} lesson(s); `deputy lessons list` shows them")
    elif proposed.lessons:
        print("\nreport only: run `deputy learn --apply` to use these in worker prompts")
    return 0


_VERDICT_NOTE = {
    "helps": "keep: it improved the benchmark",
    "neutral": "kept: no effect the benchmark can see (the suite may be too easy for it)",
    "hurts": "DROP: the run with it was worse",
    "untestable": "kept: no benchmark task carries this kind of check, so it cannot be tested",
}


def _check(args: argparse.Namespace, book: lessons.LessonBook) -> int:
    """Test each active lesson against the benchmark; with --apply, remove those that hurt."""
    now = time.time()
    active = [lesson for lesson in book.lessons if lesson.active(now)]
    if not active:
        print("no active lessons to check (run `deputy learn --apply` after some use)")
        return 0
    try:
        config = load_config()
        backend = config.backend(args.backend)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    hurting: list[str] = []
    for lesson in active:
        print(f"== {lesson.id}", file=sys.stderr, flush=True)
        result = asyncio.run(ablation.ablate(backend, lesson, runs=args.runs, policy=config.policy))
        print(_describe_ablation(result))
        if result.verdict == "hurts":
            hurting.append(lesson.id)
    if not hurting:
        print("\nno lesson hurts: nothing to remove")
    elif args.apply:
        for lesson_id in hurting:
            book = lessons.mute(book, lesson_id)
        lessons.save_book(book)
        print(f"\nremoved and muted: {', '.join(hurting)}")
    else:
        print(f"\nreport only: `deputy lessons check --apply` removes {', '.join(hurting)}")
    return 0


def _describe_ablation(result: ablation.Ablation) -> str:
    head = f"{result.lesson.id}: {_VERDICT_NOTE[result.verdict]}"
    if result.without is None or result.with_lesson is None:
        return head
    a, b = result.without, result.with_lesson
    return (
        f"{head}\n    tasks: {', '.join(result.tasks)}\n"
        f"    pass {a.pass_rate:.0%} -> {b.pass_rate:.0%}   cites {a.citation_rate:.0%} -> "
        f"{b.citation_rate:.0%}   tokens out {a.mean_tokens_out:.0f} -> {b.mean_tokens_out:.0f}"
    )


def run_lessons(args: argparse.Namespace) -> int:
    book = lessons.load_book()
    if args.action == "check":
        return _check(args, book)
    if args.action == "remove":
        if args.id not in {lesson.id for lesson in book.lessons}:
            print(f"error: no lesson '{args.id}'", file=sys.stderr)
            return 1
        lessons.save_book(lessons.mute(book, args.id))
        print(f"removed {args.id}; it will not be learned again")
        return 0
    now = time.time()
    active = [lesson for lesson in book.lessons if lesson.active(now)]
    for lesson in active:
        print(_describe(lesson, now))
    if not active:
        print("no lessons (run `deputy learn --apply` after some use)")
    if book.muted:
        print(f"\nmuted: {', '.join(book.muted)}")
    return 0


def register(sub: Subparsers) -> None:
    learn_parser = sub.add_parser(
        "learn", help="derive lessons from real use (report; --apply saves)"
    )
    learn_parser.add_argument("--days", type=float, default=30, help="how far back to look")
    learn_parser.add_argument("--apply", action="store_true", help="save the lessons")
    learn_parser.set_defaults(func=learn)

    lessons_parser = sub.add_parser("lessons", help="list the lessons, or remove one for good")
    lessons_parser.add_argument(
        "action", choices=["list", "remove", "check"], nargs="?", default="list"
    )
    lessons_parser.add_argument("id", nargs="?", help="the lesson to remove (profile:check)")
    lessons_parser.add_argument("--backend", help="check: the backend to test on")
    lessons_parser.add_argument("--runs", type=int, default=1, help="check: repetitions per side")
    lessons_parser.add_argument(
        "--apply", action="store_true", help="check: remove the lessons that hurt"
    )
    lessons_parser.set_defaults(func=run_lessons)
