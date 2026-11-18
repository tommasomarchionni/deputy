# pyright: basic
import contextlib
import os
import subprocess
import time
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from deputy.sandbox import (
    MAX_LINE_CHARS,
    MAX_PATTERN_CHARS,
    Sandbox,
    SandboxError,
    _read_text,
    _write_text,
)


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


def test_a_grep_hit_counts_only_the_hit_lines_not_the_whole_file(tmp_path: Path) -> None:
    box = make(tmp_path)
    (box.root / "big.log").write_text("\n".join(["noise"] * 99_999 + ["needle"]))
    (box.root / "other.log").write_text("nothing here\n" * 50)
    assert box.chars_read == 0
    out = box.grep("needle")
    assert box.chars_read == len(out) + 1
    assert box.chars_read < 100


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


CATASTROPHIC = ["(a+)+$", "(a|aa)+$", "(.*)*x", "([a-z]+)*$", "(a*)*b"]


@pytest.mark.parametrize("pattern", CATASTROPHIC)
def test_a_catastrophic_pattern_never_hangs(tmp_path: Path, pattern: str) -> None:
    """With Python's `re` each of these takes longer than the age of the universe on this line;
    the regex engine either finishes at once (it optimises some of them) or is cut by the limit."""
    box = make(tmp_path)
    (box.root / "victim.txt").write_text("a" * 60 + "!\n")
    started = time.monotonic()
    with contextlib.suppress(SandboxError):  # either answer is fine; hanging is not
        box.grep(pattern)
    assert time.monotonic() - started < 2.0


def test_a_pattern_that_takes_too_long_is_reported_to_the_worker(tmp_path: Path) -> None:
    box = make(tmp_path)
    (box.root / "victim.txt").write_text("a" * 60 + "!\n")
    with pytest.raises(SandboxError, match="too slow"):
        box.grep("(a|aa)+$")


def test_ordinary_patterns_behave_as_before(tmp_path: Path) -> None:
    box = make(tmp_path)
    (box.root / "a.log").write_text("ERROR code=CODE-7431 after retry\nINFO fine\n")
    assert box.grep(r"CODE-\d+") == "a.log:1: ERROR code=CODE-7431 after retry"
    assert box.grep(r"^INFO") == "a.log:2: INFO fine"
    assert box.grep("nothing") == "no matches"
    with pytest.raises(SandboxError, match="invalid pattern"):
        box.grep("(")


def test_a_very_long_pattern_is_refused(tmp_path: Path) -> None:
    box = make(tmp_path)
    with pytest.raises(SandboxError, match="too long"):
        box.grep("a" * (MAX_PATTERN_CHARS + 1))


def test_a_long_line_is_cut_before_matching(tmp_path: Path) -> None:
    box = make(tmp_path)
    (box.root / "wide.txt").write_text(
        "x" * (MAX_LINE_CHARS + 50) + "NEEDLE\n" + "NEEDLE at the start\n"
    )
    assert box.grep("NEEDLE") == "wide.txt:2: NEEDLE at the start"


def test_a_whole_search_has_a_time_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("deputy.sandbox.GREP_BUDGET_S", -1.0)  # already spent
    box = make(tmp_path)
    (box.root / "a.txt").write_text("needle\n" * 10)
    assert "time budget" in box.grep("needle")


@given(st.sampled_from(CATASTROPHIC), st.integers(min_value=1, max_value=200))
@settings(deadline=2000, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_no_catastrophic_pattern_on_any_line_length_takes_long(
    tmp_path: Path, pattern: str, length: int
) -> None:
    box = make(tmp_path) if not (tmp_path / "work").exists() else Sandbox(tmp_path / "work")
    (box.root / "line.txt").write_text("a" * length + "!\n")
    with contextlib.suppress(SandboxError):
        box.grep(pattern)


def test_grep_skips_what_git_ignores_but_finds_untracked_files(tmp_path: Path) -> None:
    box = make(tmp_path)
    subprocess.run(["git", "init", "-q", str(box.root)], check=True)
    (box.root / ".gitignore").write_text("coverage/\n")
    (box.root / "coverage").mkdir()
    (box.root / "coverage" / "report.json").write_text("needle in a generated file")
    (box.root / "src.py").write_text("needle in the source")
    out = box.grep("needle")
    assert "src.py" in out
    assert "coverage" not in out


def test_grep_still_searches_everything_outside_a_git_work_tree(tmp_path: Path) -> None:
    box = make(tmp_path)
    (box.root / "coverage").mkdir()
    (box.root / "coverage" / "report.json").write_text("needle")
    assert "coverage/report.json" in box.grep("needle")


def test_a_large_search_lists_every_file_instead_of_hiding_those_past_the_cap(
    tmp_path: Path,
) -> None:
    box = make(tmp_path)
    for i in range(30):
        (box.root / f"a{i:02d}.txt").write_text("needle\nneedle\nneedle\n")
    (box.root / "zz-last.txt").write_text("needle\n")
    out = box.grep("needle")
    assert out.startswith("[91 hits in 31 files: grouped by file")
    assert "zz-last.txt: 1 hits, lines 1" in out
    assert "a00.txt: 3 hits, lines 1, 2, 3" in out


def test_a_small_search_keeps_the_line_by_line_format(tmp_path: Path) -> None:
    box = make(tmp_path)
    (box.root / "a.txt").write_text("one\nneedle here\n")
    assert box.grep("needle") == "a.txt:2: needle here"


def test_a_file_with_many_hits_shows_its_first_lines_and_says_there_are_more(
    tmp_path: Path,
) -> None:
    box = make(tmp_path)
    (box.root / "big.txt").write_text("needle\n" * 60)
    assert "big.txt: 60 hits, lines 1, 2, 3, 4, 5, ..." in box.grep("needle")
