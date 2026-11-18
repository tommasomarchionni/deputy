# pyright: basic
"""A `feat` or `fix` that changes the product carries a test, or says in the description why not."""

from __future__ import annotations

import os
import re
import sys

from areas import is_product_path, is_test_path
from validate_pr_title import TITLE, PullRequest, pull_request_files

WAIVER = re.compile(
    r"^Regression:\s*none\s*[-\u2014\u2013]\s*(?P<reason>.+)$", re.IGNORECASE | re.MULTILINE
)
MIN_REASON_CHARS = 20
EXEMPT_AUTHORS = frozenset({"dependabot[bot]", "github-actions[bot]"})


def problem(pr: PullRequest) -> str | None:
    match = TITLE.match(pr.title)
    if match is None or match.group("type") not in ("feat", "fix") or pr.author in EXEMPT_AUTHORS:
        return None
    if not any(is_product_path(f) for f in pr.files) or any(is_test_path(f) for f in pr.files):
        return None
    waiver = WAIVER.search(pr.body)
    if waiver and len(waiver.group("reason").strip()) >= MIN_REASON_CHARS:
        return None
    return (
        "A feat or fix that changes the product needs a test in the diff (tests/), or a line in "
        "the description: 'Regression: none - <reason of at least 20 characters>'."
    )


def main() -> int:
    pr = PullRequest(
        title=os.environ["PR_TITLE"],
        body=os.environ.get("PR_BODY", ""),
        author=os.environ["PR_AUTHOR"],
        files=pull_request_files(os.environ["PR_NUMBER"]),
    )
    message = problem(pr)
    if message:
        print(f"::error::{message}")
    return 1 if message else 0


if __name__ == "__main__":
    sys.exit(main())
