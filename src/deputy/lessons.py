"""Lessons learned from use: short reminders added to the worker's prompt where it keeps failing.

A lesson is derived from evidence (how often a kind of check failed for a profile, in real runs),
its text comes from a fixed catalogue (nothing from a trace or a worker is ever copied into a
prompt), it expires, and the user can mute it. Workers cannot write lessons.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from deputy.checks import Check
from deputy.config import state_dir
from deputy.runs import BENCH_SOURCE

MIN_RUNS = 5  # runs that carried the check, before a failure rate means anything
MIN_FAILURE_RATE = 0.25
MAX_APPLIED = 3
LIFETIME_DAYS = 14
SECONDS_PER_DAY = 86_400

CATALOGUE: dict[str, str] = {
    "cites_lines": (
        "Your answers often cited no line number, or one outside the file. Cite the exact line "
        "numbers you saw (the `N:` prefix), at least one for every fact."
    ),
    "contains": (
        "Your answers often missed required facts. Before answering, list every fact the task "
        "asks for and make sure each appears, in the words of the source."
    ),
    "excludes": (
        "Your answers sometimes contained text that must not appear. Never repeat secrets, tokens "
        "or file contents you were not asked for."
    ),
    "cites_text": (
        "Your quotes often were not on the line you cited. Quote the exact words of the line, "
        "next to its number, and copy them from what you read."
    ),
    "json_schema": (
        "Your JSON often did not match the required schema. Use exactly the field names and types "
        "the task gives, include every required field, and answer with JSON only."
    ),
    "max_chars": (
        "Your answers were often too long. Keep the final answer within the length asked: no "
        "preamble, no restating the task."
    ),
    "file_written": (
        "You often did not write the file that was asked for. When a file is requested, call "
        "write_file with its full content before you answer."
    ),
    "file_contains": (
        "Files you wrote often lacked required content. Check the file's content against the task "
        "before you answer."
    ),
    "command": (
        "Code you wrote often failed its check. Keep it simple, import what the file under test "
        "provides, and use plain assert statements."
    ),
}


class Evidence(BaseModel):
    runs: int
    failures: int
    window_days: float

    @property
    def rate(self) -> float:
        return self.failures / self.runs if self.runs else 0.0


class Lesson(BaseModel):
    id: str
    profile: str
    check_kind: str
    text: str
    evidence: Evidence
    created_at: float
    expires_at: float

    def active(self, now: float) -> bool:
        return self.expires_at > now


class LessonBook(BaseModel):
    lessons: list[Lesson] = Field(default_factory=list[Lesson])
    muted: list[str] = Field(default_factory=list[str])


def _path() -> str:
    return str(state_dir() / "lessons.json")


def load_book() -> LessonBook:
    """The saved lessons; a missing or damaged file is an empty book, never an error."""
    try:
        with open(_path(), encoding="utf-8") as handle:
            return LessonBook.model_validate(json.load(handle))
    except (OSError, ValueError):
        return LessonBook()


def save_book(book: LessonBook) -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    with open(_path(), "w", encoding="utf-8") as handle:
        handle.write(book.model_dump_json(indent=2) + "\n")


@dataclass
class _Tally:
    runs: int = 0
    failures: int = 0


def derive(
    events: list[dict[str, Any]], *, days: float = 30, now: float | None = None
) -> list[Lesson]:
    """Lessons that the evidence of the last `days` days supports (real runs only, no benchmark)."""
    moment = now if now is not None else time.time()
    since = moment - days * SECONDS_PER_DAY
    tallies: dict[tuple[str, str], _Tally] = {}
    for event in events:
        if event.get("event") != "run" or float(event.get("ts") or 0) < since:
            continue
        if event.get("source") == BENCH_SOURCE:
            continue
        profile = str(event.get("profile") or "unknown")
        checks: list[dict[str, Any]] = event.get("checks") or []
        for check in checks:
            kind = str(check.get("name", "")).split(":")[0]
            tally = tallies.setdefault((profile, kind), _Tally())
            tally.runs += 1
            tally.failures += not check.get("passed", True)
    found: list[Lesson] = []
    for (profile, kind), tally in sorted(tallies.items()):
        evidence = Evidence(runs=tally.runs, failures=tally.failures, window_days=days)
        if kind in CATALOGUE and tally.runs >= MIN_RUNS and evidence.rate >= MIN_FAILURE_RATE:
            found.append(
                Lesson(
                    id=f"{profile}:{kind}",
                    profile=profile,
                    check_kind=kind,
                    text=CATALOGUE[kind],
                    evidence=evidence,
                    created_at=moment,
                    expires_at=moment + LIFETIME_DAYS * SECONDS_PER_DAY,
                )
            )
    return found


def merge(book: LessonBook, derived: list[Lesson]) -> LessonBook:
    """The new book: exactly what the evidence supports now, minus what the user muted."""
    return LessonBook(
        lessons=[lesson for lesson in derived if lesson.id not in book.muted], muted=book.muted
    )


def applicable(
    profile: str, checks: list[Check], *, now: float | None = None, book: LessonBook | None = None
) -> list[Lesson]:
    """Up to MAX_APPLIED active lessons for this profile that matter for the checks asked,
    the most frequent failures first."""
    moment = now if now is not None else time.time()
    kinds = {check.type for check in checks}
    chosen = [
        lesson
        for lesson in (book or load_book()).lessons
        if lesson.profile == profile and lesson.check_kind in kinds and lesson.active(moment)
    ]
    chosen.sort(key=lambda lesson: -lesson.evidence.rate)
    return chosen[:MAX_APPLIED]


def prompt_addendum(lessons: list[Lesson]) -> str:
    """The text added to the system prompt ('' when there is nothing to add)."""
    if not lessons:
        return ""
    lines = "\n".join(f"- {lesson.text}" for lesson in lessons)
    return f"\n\nLessons from your past runs (follow them):\n{lines}"


def mute(book: LessonBook, lesson_id: str) -> LessonBook:
    """Remove a lesson and keep it from coming back."""
    return LessonBook(
        lessons=[lesson for lesson in book.lessons if lesson.id != lesson_id],
        muted=sorted({*book.muted, lesson_id}),
    )
