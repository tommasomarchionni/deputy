# pyright: basic
import io
import json
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
    assert "`delegate`" in line


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
        "other-tool": {"tool_name": "Bash", "tool_input": {"command": f"cat {big}"}},
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
    assert out.stdout.strip() == "['deputy', 'deputy.hook', 'deputy.tokens']"


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
    assert entries == [
        {"matcher": "Read", "hooks": [{"type": "command", "command": "deputy-hook", "timeout": 5}]}
    ]
    assert "already on" in hook.enable(settings)
