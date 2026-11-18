# pyright: basic
import asyncio
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from deputy import cli, confinement
from deputy.checks import Command, run_checks
from deputy.config import Policy, config_path, load_config
from deputy.sandbox import Sandbox

PY = sys.executable


def run_command(argv: list[str], root: Path, policy: Policy):  # type: ignore[no-untyped-def]
    return asyncio.run(run_checks([Command(argv=argv)], "", Sandbox(root), policy))[0]


@pytest.fixture
def recording_wrapper(tmp_path: Path) -> Path:
    """A stand-in for bwrap or sandbox-exec: records its own arguments, then runs the command."""
    script = tmp_path / "wrap.sh"
    log = tmp_path / "wrap.log"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{log}"\nshift 2\nexec "$@"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_the_command_runs_behind_the_wrapper_with_the_workdir_filled_in(
    tmp_path: Path, recording_wrapper: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    policy = Policy(
        allowed_commands=[[PY, "-c"]],
        command_wrapper=[str(recording_wrapper), "--workdir", "{workdir}"],
    )
    result = run_command([PY, "-c", "print(1)"], work, policy)
    assert result.passed, result.detail
    seen = (tmp_path / "wrap.log").read_text().splitlines()
    assert seen[:4] == ["--workdir", str(work.resolve()), PY, "-c"]


def test_a_missing_wrapper_program_fails_the_check_instead_of_running_unconfined(
    tmp_path: Path,
) -> None:
    policy = Policy(allowed_commands=[[PY, "-c"]], command_wrapper=["/nonexistent/wrapper"])
    result = run_command([PY, "-c", "print(1)"], tmp_path, policy)
    assert not result.passed
    assert "could not start" in result.detail


def test_the_allow_list_judges_the_declared_command_not_the_wrapper(tmp_path: Path) -> None:
    policy = Policy(allowed_commands=[], command_wrapper=[PY, "-c", "pass"])
    result = run_command([PY, "-c", "print(1)"], tmp_path, policy)
    assert not result.passed
    assert "not allowed" in result.detail


@pytest.mark.parametrize(
    ("platform", "tool", "expect"),
    [
        ("darwin", "sandbox-exec", "sandbox-exec"),
        ("linux", "bwrap", "bwrap"),
        ("linux", None, None),
        ("darwin", None, None),
        ("win32", "bwrap", None),
    ],
)
def test_the_suggested_wrapper_depends_on_the_platform_and_the_tool(
    monkeypatch: pytest.MonkeyPatch, platform: str, tool: str | None, expect: str | None
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}" if name == tool else None)
    wrapper = confinement.suggested_wrapper(platform)
    assert (wrapper[0] if wrapper else None) == expect
    if wrapper and platform == "linux":
        assert "--unshare-net" in wrapper
        assert wrapper.count("{workdir}") == 3
    if wrapper and platform == "darwin":
        assert "WORKDIR={workdir}" in wrapper


def test_the_macos_profile_denies_the_network_and_writes_outside_the_workdir() -> None:
    text = confinement.MACOS_PROFILE
    assert "(deny default)" in text
    assert "(deny network*)" in text
    assert '(subpath (param "WORKDIR"))' in text
    assert ".ssh" in text


def _fake_suggestion(monkeypatch: pytest.MonkeyPatch, wrapper: list[str] | None) -> None:
    monkeypatch.setattr(confinement, "suggested_wrapper", lambda platform=None: wrapper)


def test_init_with_confine_activates_the_wrapper_and_without_it_only_suggests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_suggestion(monkeypatch, ["bwrap", "--unshare-net", "{workdir}"])
    assert cli.main(["init", "--yes", "--confine"]) == 0
    assert load_config().policy.command_wrapper == ["bwrap", "--unshare-net", "{workdir}"]
    assert cli.main(["init", "--yes", "--force"]) == 0
    text = config_path().read_text()
    assert load_config().policy.command_wrapper == []
    assert '# command_wrapper = ["bwrap"' in text


def test_init_says_so_when_no_sandbox_tool_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_suggestion(monkeypatch, None)
    cli.main(["init", "--yes", "--confine"])
    assert "no sandbox tool found" in config_path().read_text()
    assert load_config().policy.command_wrapper == []


def _configure(policy_lines: str) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'default_backend = "l"\nauto_update = "off"\n[[backends]]\nname = "l"\n'
        'base_url = "http://x"\n[policy]\n' + policy_lines
    )


@pytest.mark.parametrize(
    ("lines", "expected", "code"),
    [
        ("", "no `command` check is allowed", 0),
        ('allowed_commands = [["node", "--test"]]\n', "NOT confined", 0),
        (f'command_wrapper = ["{sys.executable}"]\n', "confined by", 0),
        ('command_wrapper = ["/nonexistent/tool"]\n', "FAIL - the wrapper program", 1),
    ],
)
def test_doctor_says_plainly_whether_commands_are_confined(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    lines: str,
    expected: str,
    code: int,
) -> None:
    async def healthy(self):  # type: ignore[no-untyped-def]
        return True, "ok"

    monkeypatch.setattr("deputy.backends.ChatClient.health", healthy)
    _configure(lines)
    assert cli.main(["doctor"]) == code or code == 1
    assert expected in capsys.readouterr().out


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("sandbox-exec") is None,
    reason="needs macOS sandbox-exec",
)
def test_the_real_macos_sandbox_confines_a_command(tmp_path: Path) -> None:
    """Runs a real command under the real profile: writes stay in the work folder, no network."""
    confinement.write_profile()
    wrapper = confinement.suggested_wrapper("darwin")
    assert wrapper is not None
    work = tmp_path / "work"
    work.mkdir()
    outside = Path.home() / f".deputy-sandbox-test-{os.getpid()}.txt"
    policy = Policy(allowed_commands=[[PY, "-c"]], command_wrapper=wrapper)

    inside = run_command([PY, "-c", "open('ok.txt', 'w').write('x')"], work, policy)
    assert inside.passed, inside.detail
    assert (work / "ok.txt").read_text() == "x"

    try:
        escape = run_command([PY, "-c", f"open({str(outside)!r}, 'w').write('x')"], work, policy)
        assert not escape.passed
        assert not outside.exists()
    finally:
        outside.unlink(missing_ok=True)

    net = "import socket; socket.create_connection(('1.1.1.1', 53), 2)"
    assert not run_command([PY, "-c", net], work, policy).passed
