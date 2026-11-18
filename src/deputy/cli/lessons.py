"""`deputy learn` derives lessons from real use; `deputy lessons` lists and mutes them."""

from __future__ import annotations

import argparse
import sys
import time

from deputy import lessons, stats
from deputy.cli._common import Subparsers


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


def run_lessons(args: argparse.Namespace) -> int:
    book = lessons.load_book()
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
    lessons_parser.add_argument("action", choices=["list", "remove"], nargs="?", default="list")
    lessons_parser.add_argument("id", nargs="?", help="the lesson to remove (profile:check)")
    lessons_parser.set_defaults(func=run_lessons)
