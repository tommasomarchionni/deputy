# pyright: basic
"""The repository automation is code like any other: its rules are tested."""

import json
from pathlib import Path

import pytest

from areas import PATH_AREAS, SCOPES, area_of, areas_of, is_product_path
from issue_triage import answers, labels_for
from pr_automation import labels_to_add, linked_issue
from regression_gate import problem as regression_problem
from sync_labels import plan
from validate_pr_title import PullRequest, problems

ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ["src/deputy/checks.py"]


def pr(title: str, body: str = "Closes #1", author: str = "me", files: list[str] | None = None):
    return PullRequest(title=title, body=body, author=author, files=files or PRODUCT)


@pytest.mark.parametrize(
    "title",
    [
        "feat(checks): add excludes",
        "fix: a bug",
        "docs(docs): typo",
        "chore(deps): bump x",
        "ci!: break",
    ],
)
def test_valid_titles_pass(title: str) -> None:
    files = (
        PRODUCT
        if title.split("(", maxsplit=1)[0].split(":", maxsplit=1)[0].rstrip("!") in ("feat", "fix")
        else ["README.md"]
    )
    assert problems(pr(title, files=files)) == []


@pytest.mark.parametrize(
    "title", ["Add things", "feat add things", "feature: x", "feat(): x", "feat: "]
)
def test_titles_that_are_not_conventional_commits_are_refused(title: str) -> None:
    assert "Conventional Commit" in problems(pr(title))[0]


def test_an_unknown_scope_is_refused_and_the_known_ones_are_listed() -> None:
    message = problems(pr("feat(nonsense): x"))[0]
    assert "Unknown scope" in message
    assert "checks" in message


def test_feat_fix_and_perf_must_change_the_product() -> None:
    for kind in ("feat", "fix", "perf"):
        assert "changes the product" in problems(pr(f"{kind}: x", files=["docs/a.md"]))[0]
    assert problems(pr("docs: x", files=["docs/a.md"])) == []


def test_every_pull_request_links_its_issue_except_bots() -> None:
    assert "Link the issue" in problems(pr("docs: x", body="no link", files=["README.md"]))[0]
    for keyword in ("Closes #3", "fixes #3", "Refs #3", "Resolves #3"):
        assert problems(pr("docs: x", body=keyword, files=["README.md"])) == []
    bot = pr("build(deps): bump", body="", author="dependabot[bot]", files=["uv.lock"])
    assert problems(bot) == []


def test_a_fix_with_a_test_passes_the_regression_gate() -> None:
    assert regression_problem(pr("fix: x", files=[*PRODUCT, "tests/test_a.py"])) is None


def test_a_fix_without_a_test_needs_a_real_reason() -> None:
    assert regression_problem(pr("fix: x")) is not None
    short = pr("fix: x", body="Closes #1\nRegression: none - tiny")
    assert regression_problem(short) is not None
    ok = pr(
        "fix: x", body="Closes #1\nRegression: none - the change is a string in an error message"
    )
    assert regression_problem(ok) is None


def test_the_regression_gate_ignores_other_kinds_of_change() -> None:
    assert regression_problem(pr("docs: x", files=["README.md"])) is None
    assert regression_problem(pr("chore: x", files=PRODUCT)) is None
    assert regression_problem(pr("fix: x", files=["docs/a.md"])) is None


def test_files_map_to_one_area_and_unknown_files_to_none() -> None:
    assert area_of("src/deputy/checks.py") == "checks"
    assert area_of("src/deputy/cli/tuning.py") == "cli"
    assert area_of("src/deputy/server.py") == "server"
    assert area_of(".github/workflows/ci.yml") == "ci"
    assert area_of("LICENSE") is None
    assert areas_of(["docs/a.md", "src/deputy/sandbox.py", "docs/b.md", "LICENSE"]) == [
        "docs",
        "sandbox",
    ]
    assert is_product_path("install.sh")
    assert not is_product_path("tests/a.py")


def test_every_area_in_the_map_has_a_label_and_every_scope_is_documented() -> None:
    labels = {item["name"] for item in json.loads((ROOT / ".github/labels.json").read_text())}
    for _, area in PATH_AREAS:
        assert f"area: {area}" in labels
    assert {"memory", "router"} <= SCOPES


def test_labels_file_is_well_formed() -> None:
    items = json.loads((ROOT / ".github/labels.json").read_text())
    names = [item["name"] for item in items]
    assert len(names) == len(set(names))
    for item in items:
        assert len(item["color"]) == 6
        assert item["description"]
        assert len(item["description"]) <= 100


def test_the_label_plan_renames_creates_and_updates() -> None:
    wanted = [
        {"name": "type: feature", "color": "1", "from": "enhancement"},
        {"name": "type: task", "color": "2"},
        {"name": "area: cli", "color": "3"},
    ]
    actions = plan(wanted, {"enhancement", "area: cli"})
    assert [a for a, _ in actions] == ["rename", "create", "update"]


def test_a_pull_request_inherits_type_and_priority_but_not_status() -> None:
    added = labels_to_add(
        ["src/deputy/checks.py"],
        ["type: bug", "priority: p1", "status: ready", "area: server"],
        {"priority: p1"},
    )
    assert added == ["area: checks", "type: bug"]


def test_the_linked_issue_is_found_in_the_description() -> None:
    assert linked_issue("blah\nCloses #42\n") == 42
    assert linked_issue("Refs #7") == 7
    assert linked_issue("nothing") is None


FORM = """### What happened

It broke

### Area

checks

### Priority

p2 - planned

### Anything else

_No response_
"""


def test_the_issue_form_answers_become_labels() -> None:
    assert answers(FORM)["area"] == "checks"
    assert labels_for(FORM, set()) == ["area: checks", "priority: p2", "status: needs-triage"]
    assert labels_for(FORM, {"status: ready", "area: checks"}) == ["priority: p2"]
