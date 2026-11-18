# pyright: basic
"""Check a pull request: a Conventional Commit title and a link to its issue.

The squash title becomes the commit that tags and release notes are built from, so it is checked
here and only here (editing a title or a description must not rerun the whole CI).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

from areas import SCOPES, is_product_path

TYPES = (
    "feat",
    "fix",
    "perf",
    "refactor",
    "docs",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
    "style",
)
PRODUCT_TYPES = ("feat", "fix", "perf")  # these start a release note: they must change the product
TITLE = re.compile(
    rf"^(?P<type>{'|'.join(TYPES)})(?:\((?P<scope>[a-z-]+)\))?(?P<breaking>!)?: (?P<subject>\S.*)$"
)
ISSUE_LINK = re.compile(r"\b(?:closes|fixes|resolves|refs)\s+#\d+", re.IGNORECASE)
BOTS = frozenset({"dependabot[bot]", "github-actions[bot]"})


@dataclass(frozen=True)
class PullRequest:
    title: str
    body: str
    author: str
    files: list[str]


def problems(pr: PullRequest) -> list[str]:
    """Everything wrong with the pull request, as sentences a contributor can act on."""
    found: list[str] = []
    match = TITLE.match(pr.title)
    if match is None:
        return [
            f"The title must be a Conventional Commit, like 'feat(cli): add a flag'. "
            f"Types: {', '.join(TYPES)}."
        ]
    scope = match.group("scope")
    if scope and scope not in SCOPES:
        found.append(f"Unknown scope '{scope}'. Use one of: {', '.join(sorted(SCOPES))}.")
    if match.group("type") in PRODUCT_TYPES and not any(is_product_path(f) for f in pr.files):
        found.append(
            f"A '{match.group('type')}' changes the product (src/deputy/ or install.sh). "
            "For tests, docs, CI or tooling use test, docs, ci, chore or build."
        )
    if pr.author not in BOTS and not ISSUE_LINK.search(pr.body):
        found.append("Link the issue in the description: 'Closes #N' (or 'Refs #N' if partial).")
    return found


def pull_request_files(number: str) -> list[str]:
    done = subprocess.run(  # noqa: S603
        ["gh", "api", f"repos/{os.environ['GH_REPO']}/pulls/{number}/files", "--paginate"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return [item["filename"] for item in json.loads(done.stdout)]


def main() -> int:
    pr = PullRequest(
        title=os.environ["PR_TITLE"],
        body=os.environ.get("PR_BODY", ""),
        author=os.environ["PR_AUTHOR"],
        files=pull_request_files(os.environ["PR_NUMBER"]),
    )
    issues = problems(pr)
    for issue in issues:
        print(f"::error::{issue}")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
