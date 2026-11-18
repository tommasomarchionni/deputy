# pyright: basic
import subprocess
from pathlib import Path

import pytest

from deputy.sandbox import Sandbox, SandboxError


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Sandbox:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "policy.md").write_text("Invoices are kept 7 years.\n")
    (tmp_path / ".env").write_text("TOKEN=SECRET-IN-HISTORY\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "first policy")
    (tmp_path / "policy.md").write_text("Invoices are kept 10 years.\n")
    (tmp_path / ".env").write_text("TOKEN=SECRET-CHANGED\n")
    _git(tmp_path, "commit", "-q", "-am", "keep invoices longer, as the auditor asked")
    return Sandbox(tmp_path)


def test_log_and_diff_show_what_changed_and_why(repo: Sandbox) -> None:
    log = repo.git(["log", "--oneline"])
    assert "keep invoices longer" in log
    diff = repo.git(["diff", "HEAD~1"])
    assert "-Invoices are kept 7 years." in diff
    assert "+Invoices are kept 10 years." in diff
    assert repo.chars_read >= len(diff)


def test_a_secret_file_never_appears_in_any_command(repo: Sandbox) -> None:
    for args in (["diff", "HEAD~1"], ["log", "-p"], ["show", "HEAD"], ["log", "--stat"]):
        out = repo.git(args)
        assert "SECRET" not in out
        assert ".env" not in out


def test_blame_of_one_file_names_the_commit(repo: Sandbox) -> None:
    assert "10 years" in repo.git(["blame", "--", "policy.md"])


@pytest.mark.parametrize(
    "args",
    [
        ["push"],
        ["commit", "-m", "x"],
        ["checkout", "HEAD~1"],
        ["config", "user.name"],
        ["diff", "--output=/tmp/x"],
        ["log", "--exec=sh"],
        ["-c", "core.pager=sh", "log"],
        ["show", "HEAD:.env"],
        ["show", "HEAD:policy.md"],
        ["diff", "--ext-diff"],
        ["log", "--", "../outside"],
        ["blame", "--", ".env"],
        ["blame", "policy.md"],
        ["log", "-n"],
        [],
    ],
)
def test_anything_outside_the_read_only_allow_list_is_refused(
    repo: Sandbox, args: list[str]
) -> None:
    with pytest.raises(SandboxError):
        repo.git(args)


def test_a_folder_that_is_not_a_repository_says_so(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="not a git repository"):
        Sandbox(tmp_path).git(["log"])


def test_options_with_values_are_accepted_in_both_spellings(repo: Sandbox) -> None:
    assert repo.git(["log", "-n", "1", "--oneline"]).count("\n") == 1
    assert repo.git(["log", "-n1", "--oneline"]).count("\n") == 1
    assert "policy.md" in repo.git(["diff", "--stat", "HEAD~1"])
