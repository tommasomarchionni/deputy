# pyright: basic
"""On a pull request: area labels from the files, type/priority/milestone from the linked issue,
and the author as assignee. Read-only on code: it only reads the file list and the issue."""

from __future__ import annotations

import os
import re
import sys

from areas import areas_of
from ghapi import gh, repo

CLOSING = re.compile(r"\b(?:closes|fixes|resolves|refs)\s+#(\d+)", re.IGNORECASE)
INHERITED_PREFIXES = ("type: ", "priority: ")


def linked_issue(body: str) -> int | None:
    match = CLOSING.search(body)
    return int(match.group(1)) if match else None


def labels_to_add(files: list[str], issue_labels: list[str], current: set[str]) -> list[str]:
    wanted = [f"area: {area}" for area in areas_of(files)]
    wanted += [name for name in issue_labels if name.startswith(INHERITED_PREFIXES)]
    return [name for name in dict.fromkeys(wanted) if name not in current]


def main() -> int:
    number = os.environ["PR_NUMBER"]
    pull = gh(f"repos/{repo()}/pulls/{number}")
    files = [f["filename"] for f in gh(f"repos/{repo()}/pulls/{number}/files", "--paginate")]
    issue_number = linked_issue(pull.get("body") or "")
    issue = gh(f"repos/{repo()}/issues/{issue_number}") if issue_number else {}
    issue_labels = [label["name"] for label in issue.get("labels", [])]
    current = {label["name"] for label in pull.get("labels", [])}

    add = labels_to_add(files, issue_labels, current)
    if add:
        gh(f"repos/{repo()}/issues/{number}/labels", "-X", "POST", payload={"labels": add})
    update: dict[str, object] = {}
    if issue.get("milestone") and not pull.get("milestone"):
        update["milestone"] = issue["milestone"]["number"]
    if update:
        gh(f"repos/{repo()}/issues/{number}", "-X", "PATCH", payload=update)
    if not pull.get("assignees") and pull["user"]["type"] == "User":
        gh(
            f"repos/{repo()}/issues/{number}/assignees",
            "-X",
            "POST",
            payload={"assignees": [pull["user"]["login"]]},
        )
    print(f"labels added: {add or 'none'}; milestone: {update.get('milestone', 'unchanged')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
