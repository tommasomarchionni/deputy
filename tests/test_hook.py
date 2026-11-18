# pyright: basic
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from deputy import cli, hook


def _read(path: Path, **extra: object) -> dict:
    return {"tool_name": "Read", "tool_input": {"file_path": str(path), **extra}, "cwd": "/"}


def test_a_large_whole_file_read_gets_a_hint_with_its_size(tmp_path: Path) -> None:
    big = tmp_path / "big.md"
    big.write_text("x" * 40_000)
    line = hook.hint(_read(big))
    assert line is not None
    assert "big.md is about 10,000 tokens" in line
    assert "`mcp__deputy__digest`" in line


@pytest.mark.parametrize("case", ["partial", "small", "missing", "other-tool", "folder", "no-path"])
def test_no_hint_when_the_read_is_small_partial_or_not_a_file_read(
    tmp_path: Path, case: str
) -> None:
    big = tmp_path / "big.md"
    big.write_text("x" * 40_000)
    small = tmp_path / "small.md"
    small.write_text("x" * 100)
    payload = {
        "partial": _read(big, offset=10, limit=50),
        "small": _read(small),
        "missing": _read(tmp_path / "nope.md"),
        "other-tool": {"tool_name": "Grep", "tool_input": {"pattern": "x", "path": str(big)}},
        "folder": _read(tmp_path),
        "no-path": {"tool_name": "Read", "tool_input": {}},
    }[case]
    assert hook.hint(payload) is None


def test_a_relative_path_is_resolved_against_the_session_folder(tmp_path: Path) -> None:
    (tmp_path / "big.md").write_text("x" * 40_000)
    payload = {"tool_name": "Read", "tool_input": {"file_path": "big.md"}, "cwd": str(tmp_path)}
    assert hook.hint(payload) is not None


def test_the_hook_output_adds_context_and_never_blocks(tmp_path: Path) -> None:
    big = tmp_path / "big.md"
    big.write_text("x" * 40_000)
    out = json.loads(hook.run(json.dumps(_read(big))))
    assert out == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": hook.hint(_read(big)),
        }
    }
    assert "permissionDecision" not in out["hookSpecificOutput"]
    assert hook.run("not json") == ""
    assert hook.run("[]") == ""


def test_the_command_prints_the_hint_and_always_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    big = tmp_path / "big.md"
    big.write_text("x" * 40_000)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_read(big))))
    assert cli.main(["hook", "read-hint"]) == 0
    assert "additionalContext" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO("garbage"))
    assert cli.main(["hook", "read-hint"]) == 0
    assert capsys.readouterr().out == ""


def test_on_and_off_keep_every_other_setting_and_hook(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}
    settings.write_text(json.dumps({"model": "opus", "hooks": {"PreToolUse": [other]}}))
    assert "on" in hook.enable(settings)
    assert "already on" in hook.enable(settings)
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    assert other in data["hooks"]["PreToolUse"]
    assert len(data["hooks"]["PreToolUse"]) == 2
    assert hook.is_enabled(settings)
    assert "off" in hook.disable(settings)
    assert json.loads(settings.read_text()) == {"model": "opus", "hooks": {"PreToolUse": [other]}}
    assert "already off" in hook.disable(settings)
    assert not hook.is_enabled(settings)


def test_on_creates_the_settings_file_and_off_leaves_no_empty_hooks(tmp_path: Path) -> None:
    settings = tmp_path / "claude" / "settings.json"
    hook.enable(settings)
    assert hook.is_enabled(settings)
    hook.disable(settings)
    assert json.loads(settings.read_text()) == {}


def test_a_broken_settings_file_is_reported_not_overwritten(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("[1, 2]")
    with pytest.raises(ValueError, match="not a JSON object"):
        hook.enable(settings)
    assert settings.read_text() == "[1, 2]"


def test_the_hook_entry_point_stays_light() -> None:
    code = (
        "import sys, deputy.hook; print(sorted(m for m in sys.modules if m.startswith('deputy')))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "['deputy', 'deputy.hook', 'deputy.shellreads', 'deputy.tokens']"


def test_off_also_removes_an_entry_written_with_the_long_command(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    old = {"matcher": "Read", "hooks": [{"type": "command", "command": "deputy hook read-hint"}]}
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [old]}}))
    hook.disable(settings)
    assert json.loads(settings.read_text()) == {}


def test_the_hook_entry_has_a_short_timeout_and_on_upgrades_an_old_entry(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    old = {"matcher": "Read", "hooks": [{"type": "command", "command": "deputy-hook"}]}
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [old]}}))
    hook.enable(settings)
    entries = json.loads(settings.read_text())["hooks"]["PreToolUse"]
    command = {"type": "command", "command": "deputy-hook", "timeout": 5}
    assert entries == [{"matcher": "Read|Bash", "hooks": [command]}]
    assert "already on" in hook.enable(settings)


@pytest.mark.parametrize("name", ["studio-1440-light.png", "report.PDF", "bundle.tar.gz"])
def test_no_hint_for_a_file_the_worker_cannot_read(tmp_path: Path, name: str) -> None:
    # The real case: a screenshot was announced as "about 73,534 tokens" to delegate.
    file = tmp_path / name
    file.write_bytes(b"x" * 40_000)
    assert hook.hint(_read(file)) is None


def test_no_hint_for_binary_content_under_a_text_name(tmp_path: Path) -> None:
    file = tmp_path / "data.txt"
    file.write_bytes(b"abc\0" * 10_000)
    assert hook.hint(_read(file)) is None


def _shell(command: str, cwd: Path) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}


def _lines_file(folder: Path) -> Path:
    path = folder / "long.md"
    path.write_text("".join(f"{i:04d} " + "x" * 94 + "\n" for i in range(1, 501)))  # 100 bytes each
    return path


@pytest.mark.parametrize(
    "command",
    [
        "cat long.md",
        "nl -ba long.md",
        "sed -n 1,200p long.md",
        "head -n 200 long.md",
        "tail -n 200 long.md",
        "tail -n +200 long.md",
        "cd sub && cat ../long.md",
        "cat long.md 2>/dev/null",
    ],
)
def test_a_large_shell_read_gets_a_hint(tmp_path: Path, command: str) -> None:
    _lines_file(tmp_path)
    (tmp_path / "sub").mkdir()
    line = hook.hint(_shell(command, tmp_path))
    assert line is not None
    assert "long.md is about" in line


def test_the_hint_counts_only_the_lines_printed(tmp_path: Path) -> None:
    _lines_file(tmp_path)
    line = hook.hint(_shell("sed -n 1,100p long.md", tmp_path))
    assert line is not None
    assert "about 2,500 tokens" in line


def test_several_files_add_up(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("x" * 5_000)
    (tmp_path / "b.md").write_text("x" * 5_000)
    line = hook.hint(_shell("cat a.md b.md", tmp_path))
    assert line is not None
    assert "These 2 files are about 2,500 tokens" in line


@pytest.mark.parametrize(
    "command",
    [
        "sed -n 1,50p long.md",
        "head -n 50 long.md",
        "tail -n 50 long.md",
        "cat long.md | grep 0001",
        "sed -n '/0001/,/0200/p' long.md",
        "sed -i '' 's/x/y/' long.md",
        "cat long.md > copy.md",
        "tail -f long.md",
        "cat missing.md",
        "cat picture.png",
        "cat noise.dat",
        "cat .",
        "git log",
        "cat 'unterminated",
        "",
    ],
)
def test_no_hint_for_a_shell_command_that_prints_little_or_cannot_be_told(
    tmp_path: Path, command: str
) -> None:
    _lines_file(tmp_path)
    (tmp_path / "picture.png").write_bytes(b"x" * 40_000)
    (tmp_path / "noise.dat").write_bytes(b"\0" * 40_000)
    assert hook.hint(_shell(command, tmp_path)) is None


def test_a_named_pipe_is_never_opened(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "pipe")
    assert hook.hint(_shell("cat pipe", tmp_path)) is None


def test_the_hook_covers_reads_and_shell_commands(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    hook.enable(target)
    (entry,) = json.loads(target.read_text())["hooks"]["PreToolUse"]
    assert entry["matcher"] == "Read|Bash"


def test_an_installed_hint_is_widened_and_one_turned_off_stays_off(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    command = {"type": "command", "command": hook.HOOK_COMMAND, "timeout": hook.HOOK_TIMEOUT_S}
    old = {"matcher": "Read", "hooks": [command]}
    other = {"matcher": "Write", "hooks": [{"type": "command", "command": "mine"}]}
    target.write_text(json.dumps({"hooks": {"PreToolUse": [other, old]}}))
    assert hook.refresh(target) is True
    entries = json.loads(target.read_text())["hooks"]["PreToolUse"]
    assert entries[0] == other
    assert entries[1]["matcher"] == "Read|Bash"
    assert hook.refresh(target) is False
    hook.disable(target)
    before = target.read_text()
    assert hook.refresh(target) is False
    assert target.read_text() == before


def test_a_broken_settings_file_is_left_alone(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("{broken")
    assert hook.refresh(target) is False
    assert target.read_text() == "{broken"
