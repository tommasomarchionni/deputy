# pyright: basic
from pathlib import Path

import pytest

from deputy import cli, workspace


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n")
    (root / "README.md").write_text("hi\n")
    (root / ".env").write_text("TOKEN=1\n")
    return root


def test_create_copies_files_and_skips_secrets(repo: Path) -> None:
    work = workspace.create("job", repo, ["src", "README.md", ".env"])
    assert (work / "src" / "a.py").read_text() == "x = 1\n"
    assert (work / "README.md").exists()
    assert not (work / ".env").exists()


def test_diff_shows_changed_added_and_removed_files(repo: Path) -> None:
    work = workspace.create("job", repo, ["src", "README.md"])
    assert workspace.diff("job") == ""
    (work / "src" / "a.py").write_text("x = 2\n")
    (work / "new.txt").write_text("new\n")
    (work / "README.md").unlink()
    text = workspace.diff("job")
    assert "-x = 1" in text
    assert "+x = 2" in text
    assert "+++ b/new.txt" in text
    assert "--- a/README.md" in text
    assert (repo / "src" / "a.py").read_text() == "x = 1\n"  # the real repository is untouched


@pytest.mark.parametrize("bad", ["", "../x", "a/b", ".hidden", "x" * 70])
def test_names_are_validated(repo: Path, bad: str) -> None:
    with pytest.raises(workspace.WorkspaceError):
        workspace.create(bad, repo, ["README.md"])


def test_paths_outside_the_repository_or_missing_are_refused(repo: Path) -> None:
    with pytest.raises(workspace.WorkspaceError, match="outside"):
        workspace.create("job", repo, ["../elsewhere"])
    with pytest.raises(workspace.WorkspaceError, match="does not exist"):
        workspace.create("job", repo, ["nope.txt"])
    with pytest.raises(workspace.WorkspaceError, match="at least one"):
        workspace.create("job", repo, [])


def test_a_workspace_is_not_overwritten_and_can_be_removed(repo: Path) -> None:
    workspace.create("job", repo, ["README.md"])
    with pytest.raises(workspace.WorkspaceError, match="exists"):
        workspace.create("job", repo, ["README.md"])
    workspace.remove("job")
    with pytest.raises(workspace.WorkspaceError, match="no workspace"):
        workspace.remove("job")
    with pytest.raises(workspace.WorkspaceError, match="no workspace"):
        workspace.diff("job")


def test_binary_files_in_a_diff_are_reported_not_crashed_on(repo: Path) -> None:
    work = workspace.create("job", repo, ["README.md"])
    (work / "blob.bin").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(workspace.WorkspaceError, match="UTF-8"):
        workspace.diff("job")


def test_cli_workspace_round_trip(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["workspace", "create", "job", "README.md", "--repo", str(repo)]) == 0
    path = Path(capsys.readouterr().out.strip())
    (path / "README.md").write_text("changed\n")
    assert cli.main(["workspace", "diff", "job"]) == 0
    assert "+changed" in capsys.readouterr().out
    assert cli.main(["workspace", "path", "job"]) == 0
    assert cli.main(["workspace", "remove", "job"]) == 0
    assert cli.main(["workspace", "diff", "job"]) == 1
