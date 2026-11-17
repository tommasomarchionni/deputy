# pyright: basic
import json
from pathlib import Path

import pytest

from deputy import cli, install
from deputy.config import config_path


def test_every_agent_has_a_snippet() -> None:
    for agent in install.AGENTS:
        assert "deputy" in install.snippet(agent)


def test_vscode_and_cursor_snippets_are_valid_json_with_the_right_keys() -> None:
    assert json.loads(install.snippet("vscode"))["servers"]["deputy"]["type"] == "stdio"
    assert "deputy" in json.loads(install.snippet("cursor"))["mcpServers"]


def test_apply_merges_without_clobbering_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / ".vscode" / "mcp.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"servers": {"other": {"command": "x"}}, "inputs": []}))
    assert "added" in install.apply("vscode", tmp_path)
    data = json.loads(path.read_text())
    assert set(data["servers"]) == {"other", "deputy"}
    assert data["inputs"] == []
    assert "already present" in install.apply("vscode", tmp_path)


def test_apply_refuses_to_overwrite_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / ".cursor" / "mcp.json"
    path.parent.mkdir()
    path.write_text("{not json")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        install.apply("cursor", tmp_path)
    assert path.read_text() == "{not json"


def test_apply_is_not_supported_for_codex(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not supported"):
        install.apply("codex", tmp_path)


def test_instructions_are_inserted_then_refreshed_in_place(tmp_path: Path) -> None:
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("# Mine\n\nkeep me\n")
    assert "added" in install.write_instructions(doc)
    assert "updated" in install.write_instructions(doc)
    text = doc.read_text()
    assert text.count(install.MARK_START) == 1
    assert "keep me" in text
    assert "delegate" in text


def test_cli_install_prints_and_does_not_touch_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["install", "vscode"]) == 0
    assert "printed only" in capsys.readouterr().out
    assert not (tmp_path / ".vscode").exists()


def test_init_writes_a_private_config_and_refuses_to_overwrite(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["init", "--yes", "--url", "http://h:1", "--model", "m"]) == 0
    target = config_path()
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert 'base_url = "http://h:1"' in target.read_text()
    assert cli.main(["init", "--yes"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_doctor_without_config_fails_with_a_hint(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["doctor"]) == 1
    assert "deputy init" in capsys.readouterr().out
