from pathlib import Path

from deputy import small_input
from deputy.sandbox import Sandbox


def _box(tmp_path: Path, **files: int) -> Sandbox:
    for name, size in files.items():
        (tmp_path / name.replace("_", ".")).write_text("x" * size)
    return Sandbox(tmp_path)


def test_a_task_naming_only_a_short_file_is_flagged_with_the_estimate(tmp_path: Path) -> None:
    box = _box(tmp_path, notes_md=400)
    out = small_input.advice("Summarise notes.md in one line.", box)
    assert out is not None
    assert out.startswith(small_input.SMALL_INPUT_FLAG)
    assert "about 100 tokens" in out


def test_a_task_naming_a_long_file_is_not_flagged(tmp_path: Path) -> None:
    box = _box(tmp_path, big_log=40_000)
    assert small_input.advice("Find the error in big.log", box) is None


def test_the_sizes_of_all_named_files_add_up(tmp_path: Path) -> None:
    box = _box(tmp_path, a_md=3_000, b_md=3_000)
    assert small_input.advice("Compare a.md with b.md.", box) is None


def test_a_task_naming_no_existing_file_gets_no_flag(tmp_path: Path) -> None:
    assert (
        small_input.advice("Summarise missing.md and the whole project", Sandbox(tmp_path)) is None
    )


def test_a_secret_or_outside_path_is_ignored_not_counted(tmp_path: Path) -> None:
    box = _box(tmp_path, notes_md=400)
    (tmp_path / ".env").write_text("K=1")
    out = small_input.advice("Read .env, ../outside.txt and notes.md", box)
    assert out is not None
    assert "about 100 tokens" in out


def test_a_trailing_full_stop_does_not_hide_the_file(tmp_path: Path) -> None:
    box = _box(tmp_path, notes_md=40)
    assert small_input.named_input_tokens("Read notes.md.", box) == 10
