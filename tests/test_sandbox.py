# pyright: basic
from pathlib import Path

import pytest

from deputy.sandbox import Sandbox, SandboxError


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
