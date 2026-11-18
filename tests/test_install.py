# pyright: basic
import json
import subprocess
from pathlib import Path

import pytest

from deputy import cli, hook, install, tokens
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


@pytest.mark.parametrize("target", sorted(install.INSTRUCTION_TARGETS))
def test_every_target_renders_the_guide(target: str) -> None:
    text = install.render_instructions(target)
    assert "deputy" in text
    assert "`delegate`" in text
    spec = install.INSTRUCTION_TARGETS[target]
    assert text.startswith("---\n") == (spec.frontmatter is not None)


def test_owned_files_have_the_front_matter_their_agent_reads() -> None:
    assert install.render_instructions("claude").startswith("---\nname: deputy\ndescription: ")
    assert 'applyTo: "**"' in install.render_instructions("copilot")
    assert "alwaysApply: false" in install.render_instructions("cursor")


def test_apply_writes_owned_files_whole_and_shared_files_as_a_block(tmp_path: Path) -> None:
    assert "wrote" in install.apply_instructions("copilot", tmp_path)
    owned = tmp_path / ".github/instructions/deputy.instructions.md"
    assert owned.read_text() == install.render_instructions("copilot")

    shared = tmp_path / "AGENTS.md"
    shared.write_text("# Mine\n\nkeep me\n")
    install.apply_instructions("agents", tmp_path)
    install.apply_instructions("agents", tmp_path)  # refreshed in place, not duplicated
    text = shared.read_text()
    assert text.count(install.MARK_START) == 1
    assert "keep me" in text


def test_the_cli_prints_applies_and_keeps_the_legacy_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["instructions", "--for", "claude"]) == 0
    assert capsys.readouterr().out.startswith("---\nname: deputy")
    assert not (tmp_path / ".claude").exists()
    assert cli.main(["instructions", "--for", "claude", "--apply"]) == 0
    assert (tmp_path / ".claude/skills/deputy/SKILL.md").is_file()
    assert cli.main(["instructions", "--write", str(tmp_path / "NOTES.md")]) == 0
    assert install.MARK_START in (tmp_path / "NOTES.md").read_text()
    assert cli.main(["instructions"]) == 0
    assert "Delegating to deputy" in capsys.readouterr().out


def test_the_delegation_guide_states_the_measured_rules_and_stays_short() -> None:
    guide = (Path(__file__).parents[1] / "src/deputy/instructions.md").read_text()
    for rule in ("more than about 4 KB", "do not read it first", "none of your context", "lookup"):
        assert rule in guide
    assert tokens.estimate(guide) <= 500  # the frontier model pays for it whenever it is loaded


def test_the_installer_refuses_a_uv_installer_that_does_not_match_its_checksum(
    tmp_path: Path,
) -> None:
    fake = tmp_path / "uv-installer.sh"
    fake.write_text("echo 'this must never run' > ran.txt\n")
    script = Path(__file__).parents[1] / "install.sh"
    done = subprocess.run(
        ["/bin/sh", str(script), "--install-uv"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "UV_INSTALLER_URL": fake.as_uri()},
        check=False,
    )
    assert done.returncode != 0
    assert "does not match its pinned SHA-256" in done.stderr
    assert not (tmp_path / "ran.txt").exists()


def _fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    return tmp_path / "claude" / "settings.json"


def test_installing_for_claude_code_turns_the_read_hint_on_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _fake_claude(tmp_path, monkeypatch)
    report = install.apply("claude-code", tmp_path)
    assert "registered with Claude Code" in report
    assert "read hint on" in report
    assert hook.is_enabled(settings)


def test_installing_with_no_hook_leaves_the_settings_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _fake_claude(tmp_path, monkeypatch)
    install.apply("claude-code", tmp_path, read_hint=False)
    assert not settings.exists()


def test_installing_for_claude_code_writes_the_user_level_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_claude(tmp_path, monkeypatch)
    report = install.apply("claude-code", tmp_path, read_hint=False)
    skill = tmp_path / "claude" / "skills" / "deputy" / "SKILL.md"
    assert "skill installed" in report
    assert skill.read_text() == install.render_instructions("claude")
    install.apply("claude-code", tmp_path / "other", read_hint=False, skill=False)  # no error


def test_the_user_skill_is_refreshed_only_when_it_exists_and_is_stale(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "deputy" / "SKILL.md"
    assert install.refresh_user_skill(skill) is False  # never created by a refresh
    assert not skill.exists()
    skill.parent.mkdir(parents=True)
    skill.write_text("an old guide\n")
    assert install.refresh_user_skill(skill) is True
    assert skill.read_text() == install.render_instructions("claude")
    assert install.refresh_user_skill(skill) is False  # already current


def test_doctor_names_a_stale_project_copy_of_the_skill(tmp_path: Path) -> None:
    assert install.stale_project_skill(tmp_path) is None
    copy = tmp_path / ".claude" / "skills" / "deputy" / "SKILL.md"
    copy.parent.mkdir(parents=True)
    copy.write_text(install.render_instructions("claude"))
    assert install.stale_project_skill(tmp_path) is None  # current: fine
    copy.write_text("an old guide\n")
    warning = install.stale_project_skill(tmp_path)
    assert warning is not None
    assert "older copy of the delegation guide" in warning
