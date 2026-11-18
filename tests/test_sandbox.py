# pyright: basic
import os
from pathlib import Path

import pytest

from deputy.sandbox import Sandbox, SandboxError, _read_text, _write_text


def make(tmp_path: Path) -> Sandbox:
    (tmp_path / "work").mkdir()
    return Sandbox(tmp_path / "work")


def test_reads_numbered_lines_and_reports_paging(tmp_path: Path) -> None:
    box = make(tmp_path)
    (box.root / "a.txt").write_text("\n".join(f"row {i}" for i in range(1, 11)))
    out = box.read_file("a.txt", offset=3, limit=2)
    assert out.splitlines() == ["3: row 3", "4: row 4", "[lines 3-4 of 10] read more with offset=5"]


def test_refuses_parent_traversal_and_absolute_paths_outside(tmp_path: Path) -> None:
    box = make(tmp_path)
    (tmp_path / "secret.txt").write_text("x")
    for bad in ["../secret.txt", str(tmp_path / "secret.txt"), "a/../../secret.txt"]:
        with pytest.raises(SandboxError):
            box.read_file(bad)


def test_refuses_a_symlink_that_points_outside(tmp_path: Path) -> None:
    box = make(tmp_path)
    (tmp_path / "outside.txt").write_text("private")
    (box.root / "link.txt").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(SandboxError):
        box.read_file("link.txt")
    with pytest.raises(SandboxError):
        box.write_file("link.txt", "overwrite")
    assert (tmp_path / "outside.txt").read_text() == "private"


@pytest.mark.parametrize("name", [".env", ".env.local", "server.pem", "id_rsa", "deploy.key"])
def test_secret_looking_files_are_never_accessible(tmp_path: Path, name: str) -> None:
    box = make(tmp_path)
    (box.root / name).write_text("TOKEN=1")
    with pytest.raises(SandboxError):
        box.read_file(name)
    with pytest.raises(SandboxError):
        box.write_file(name, "x")


def test_write_creates_folders_and_records_the_file(tmp_path: Path) -> None:
    box = make(tmp_path)
    box.write_file("sub/dir/new.txt", "hello")
    assert (box.root / "sub/dir/new.txt").read_text() == "hello"
    assert box.files_written == ["sub/dir/new.txt"]


def test_grep_finds_lines_skips_secrets_and_reports_no_match(tmp_path: Path) -> None:
    box = make(tmp_path)
    (box.root / "a.txt").write_text("alpha\nbeta needle\n")
    (box.root / ".env").write_text("needle=1")
    assert box.grep("needle") == "a.txt:2: beta needle"
    assert box.grep("absent") == "no matches"
    with pytest.raises(SandboxError):
        box.grep("(")


def test_binary_files_are_refused(tmp_path: Path) -> None:
    box = make(tmp_path)
    (box.root / "b.bin").write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(SandboxError):
        box.read_file("b.bin")


def test_a_missing_working_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SandboxError):
        Sandbox(tmp_path / "nope")


def test_empty_and_null_paths_are_refused(tmp_path: Path) -> None:
    box = make(tmp_path)
    for bad in ["", "a\x00b"]:
        with pytest.raises(SandboxError):
            box.resolve(bad)


def test_reading_a_directory_and_oversized_writes_are_refused(tmp_path: Path) -> None:
    box = make(tmp_path)
    (box.root / "d").mkdir()
    with pytest.raises(SandboxError):
        box.read_file("d")
    with pytest.raises(SandboxError):
        box.write_file("big.txt", "x" * 1_000_001)


def test_a_very_long_read_is_cut_and_says_where_to_continue(tmp_path: Path) -> None:
    box = make(tmp_path)
    (box.root / "long.txt").write_text("\n".join("y" * 200 for _ in range(1000)))
    out = box.read_file("long.txt", limit=1000)
    assert "read more with offset=" in out


def test_a_grep_hit_counts_the_whole_file_as_seen_by_the_worker(tmp_path: Path) -> None:
    box = make(tmp_path)
    (box.root / "big.log").write_text("\n".join(["noise"] * 999 + ["needle"]))
    (box.root / "other.log").write_text("nothing here\n" * 50)
    assert box.chars_read == 0
    box.grep("needle")
    assert box.chars_read >= 999 * 6


def test_grep_never_reads_through_a_symlink_that_points_outside(tmp_path: Path) -> None:
    """Found by the review of issue 23: grep used to follow such a link and leak the file."""
    box = make(tmp_path)
    (tmp_path / "outside.txt").write_text("TOP-SECRET-OUTSIDE\n")
    (box.root / "link.txt").symlink_to(tmp_path / "outside.txt")
    (box.root / "inside.txt").write_text("a plain line\n")
    assert box.grep("TOP-SECRET") == "no matches"
    assert box.grep("plain") == "inside.txt:1: a plain line"
    with pytest.raises(SandboxError):  # asking for the link itself is refused outright
        box.grep("TOP-SECRET", "link.txt")


def test_a_hard_link_to_an_outside_file_is_never_written_through(tmp_path: Path) -> None:
    box = make(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("precious")
    (box.root / "alias.txt").hardlink_to(outside)
    with pytest.raises(SandboxError):
        box.write_file("alias.txt", "overwritten")
    assert outside.read_text() == "precious"


def test_a_symlink_swapped_in_after_the_check_is_not_followed(tmp_path: Path) -> None:
    """The final open refuses a symlink even if one appears after `resolve` returned."""
    box = make(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    swapped = box.root / "swapped.txt"
    swapped.symlink_to(outside)
    with pytest.raises(SandboxError):
        _read_text(swapped)
    with pytest.raises(SandboxError):
        _write_text(swapped, "x")
    assert outside.read_text() == "private"


def test_a_fifo_is_refused_instead_of_blocking(tmp_path: Path) -> None:
    box = make(tmp_path)
    os.mkfifo(box.root / "pipe")
    with pytest.raises(SandboxError):
        box.read_file("pipe")
    assert box.grep("anything") == "no matches"


def test_a_file_over_the_size_cap_is_refused_and_skipped_by_grep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("deputy.sandbox.MAX_FILE_BYTES", 100)
    box = make(tmp_path)
    (box.root / "big.log").write_text("needle\n" * 100)
    (box.root / "small.log").write_text("needle\n")
    with pytest.raises(SandboxError, match="too large"):
        box.read_file("big.log")
    assert box.grep("needle") == "small.log:1: needle"


@pytest.mark.parametrize(
    "name",
    [
        ".git/config",
        "sub/.ssh/id_rsa",
        ".aws/credentials",
        "credentials.json",
        ".pypirc",
        "app.keystore",
        "secrets.yml",
        "deep/.GIT/HEAD",
        ".git-credentials",
    ],
)
def test_more_secret_looking_paths_are_never_accessible(tmp_path: Path, name: str) -> None:
    box = make(tmp_path)
    target = box.root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("TOKEN=1")
    with pytest.raises(SandboxError):
        box.read_file(name)
    with pytest.raises(SandboxError):
        box.write_file(name, "x")
    assert box.grep("TOKEN") == "no matches"


def test_ordinary_names_that_resemble_secrets_are_still_readable(tmp_path: Path) -> None:
    box = make(tmp_path)
    for name in ("environment.md", "keys.md", "credits.txt", "gitignore.txt", "secretary.md"):
        (box.root / name).write_text("fine\n")
        assert "fine" in box.read_file(name)
