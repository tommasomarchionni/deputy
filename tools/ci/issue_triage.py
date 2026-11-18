# pyright: basic
"""On a new issue: turn the answers of the issue form into labels, and mark it for triage."""

from __future__ import annotations

import os
import re
import sys

from ghapi import gh, repo

FIELD = re.compile(
    r"^### (?P<name>.+?)\s*\n\n(?P<value>.*?)(?=\n### |\Z)", re.MULTILINE | re.DOTALL
)
ANSWERED = {"area": "area: ", "priority": "priority: "}
NO_ANSWER = "_No response_"


def answers(body: str) -> dict[str, str]:
    return {m.group("name").strip().lower(): m.group("value").strip() for m in FIELD.finditer(body)}


def labels_for(body: str, current: set[str]) -> list[str]:
    """Labels implied by the form, plus needs-triage when no status was set yet."""
    wanted: list[str] = []
    for field, value in answers(body).items():
        prefix = ANSWERED.get(field)
        if prefix and value and value != NO_ANSWER:
            wanted.append(f"{prefix}{value.lower().split(' ')[0]}")
    if not any(name.startswith("status: ") for name in current):
        wanted.append("status: needs-triage")
    return [name for name in dict.fromkeys(wanted) if name not in current]


def main() -> int:
    number = os.environ["ISSUE_NUMBER"]
    issue = gh(f"repos/{repo()}/issues/{number}")
    current = {label["name"] for label in issue.get("labels", [])}
    add = labels_for(issue.get("body") or "", current)
    if add:
        gh(f"repos/{repo()}/issues/{number}/labels", "-X", "POST", payload={"labels": add})
    print(f"labels added: {add or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
