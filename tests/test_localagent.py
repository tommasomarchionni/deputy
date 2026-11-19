# pyright: basic
import asyncio
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from deputy import localagent
from deputy.checks import Contains
from deputy.config import Backend, ConfigError, Policy, state_dir

BACKEND = Backend(name="b", base_url="http://model", model="local-model")


class FakeGateway:
    url = "http://127.0.0.1:1"
    token = "gateway-token"

    def __init__(self, backend: Backend) -> None:
        self.backend = backend

    async def __aenter__(self) -> "FakeGateway":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def _git(cwd: Path, *args: str) -> str:
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    done = subprocess.run(
        ["git", *identity, *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "start")
    return root


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A `claude` program that does what the test says, in the folder it is started in."""
    folder = tmp_path / "bin"
    folder.mkdir()

    def install(shell: str, subtype: str = "success", summary: str = "added the file") -> None:
        event = json.dumps({"type": "result", "subtype": subtype, "result": summary})
        tool = json.dumps(
            {"message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash"}]}}
        )
        program = folder / "claude"
        program.write_text(f"#!/bin/sh\n{shell}\necho '{tool}'\necho '{event}'\n")
        program.chmod(program.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(localagent, "Gateway", FakeGateway)
    return install


def _run(repo: Path, checks: list | None = None):
    return asyncio.run(localagent.run("do it", repo, BACKEND, checks or [], Policy(), "run-1"))


def test_the_agent_works_in_a_copy_and_returns_a_patch(repo: Path, fake_claude) -> None:
    fake_claude("echo two >> a.txt; echo new > b.txt")
    result = _run(repo)
    assert result.status == "ok"
    assert (repo / "a.txt").read_text() == "one\n"
    assert not (repo / "b.txt").exists()
    patch = state_dir() / "agents" / "run-1.patch"
    assert str(patch) in result.answer
    _git(repo, "apply", str(patch))
    assert (repo / "a.txt").read_text() == "one\ntwo\n"
    assert (repo / "b.txt").read_text() == "new\n"


def test_the_copy_starts_from_uncommitted_work_and_is_removed(repo: Path, fake_claude) -> None:
    (repo / "a.txt").write_text("edited\n")
    fake_claude('test "$(cat a.txt)" = edited && echo seen > seen.txt')
    assert _run(repo).status == "ok"
    patch = (state_dir() / "agents" / "run-1.patch").read_text()
    assert "seen.txt" in patch
    assert "a.txt" not in patch
    assert not (state_dir() / "agents" / "run-1").exists()
    assert str(state_dir()) not in _git(repo, "worktree", "list")


def test_a_failing_check_hands_the_task_back(repo: Path, fake_claude) -> None:
    fake_claude("echo two >> a.txt")
    result = _run(repo, [Contains(values=["never said"])])
    assert result.status == "checks_failed"
    assert "needs_frontier" in result.flags


def test_an_agent_that_does_not_finish_hands_the_task_back(repo: Path, fake_claude) -> None:
    fake_claude("true", subtype="error_during_execution")
    result = _run(repo)
    assert result.status == "error"
    assert "needs_frontier" in result.flags


def test_a_folder_that_is_not_a_repository_is_refused(tmp_path: Path, fake_claude) -> None:
    fake_claude("true")
    with pytest.raises(ConfigError, match="git rev-parse failed"):
        _run(tmp_path)


def test_generated_files_are_not_part_of_the_patch(repo: Path, fake_claude) -> None:
    fake_claude("mkdir __pycache__; echo x > __pycache__/a.pyc; echo two >> a.txt")
    assert _run(repo).status == "ok"
    patch = (state_dir() / "agents" / "run-1.patch").read_text()
    assert "a.txt" in patch
    assert "pyc" not in patch


def test_the_tools_of_the_agent_stay_in_its_copy(tmp_path: Path) -> None:
    rules = localagent.agent_settings(tmp_path)["permissions"]
    inside = f"/{tmp_path.resolve()}/**"
    assert {f"Read({inside})", f"Edit({inside})", f"Write({inside})"} <= set(rules["allow"])
    assert not {"Read", "Edit", "Write"} & set(rules["allow"])
    assert rules["defaultMode"] == "dontAsk"


def test_the_shell_of_the_agent_is_confined(tmp_path: Path) -> None:
    sandbox = localagent.agent_settings(tmp_path)["sandbox"]
    assert sandbox["enabled"]
    assert sandbox["failIfUnavailable"]
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["network"]["allowedDomains"] == []
    assert {"~/.ssh", "~/.config/deputy", "~/.claude"} <= set(sandbox["filesystem"]["denyRead"])
    assert localagent.agent_settings(tmp_path)["disableAllHooks"] is True


def test_the_agent_gets_no_credential_of_the_user(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "secret")
    env = localagent.agent_environment(cast("Any", FakeGateway(BACKEND)), BACKEND, Path("/copy"))
    assert "secret" not in env.values()
    assert env["ANTHROPIC_AUTH_TOKEN"] == "gateway-token"
    assert env["ANTHROPIC_BASE_URL"] == FakeGateway.url
    assert env["PYTHONPATH"] == "/copy/src"


def _context(monkeypatch: pytest.MonkeyPatch, reply: httpx.Response) -> None:
    transport = httpx.MockTransport(lambda _: reply)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))


def test_a_server_with_a_small_context_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    props = {"default_generation_settings": {"n_ctx": 32_768}}
    _context(monkeypatch, httpx.Response(200, json=props))
    with pytest.raises(ConfigError, match="--ctx-size 131072"):
        asyncio.run(localagent.require_context(BACKEND))


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, json={"default_generation_settings": {"n_ctx": 131_072}}),
        httpx.Response(404),
    ],
)
def test_a_large_or_unknown_context_is_accepted(
    monkeypatch: pytest.MonkeyPatch, reply: httpx.Response
) -> None:
    _context(monkeypatch, reply)
    asyncio.run(localagent.require_context(BACKEND))


def test_ignored_dependencies_linked_into_the_copy_do_not_break_the_patch(
    repo: Path, fake_claude
) -> None:
    (repo / ".gitignore").write_text(".venv\nnode_modules\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ignore")
    (repo / ".venv").mkdir()
    (repo / "node_modules").mkdir()
    fake_claude("echo two >> a.txt")
    result = _run(repo)
    assert result.status == "ok"
    patch = (state_dir() / "agents" / "run-1.patch").read_text()
    assert "a.txt" in patch
    assert ".venv" not in patch
    assert "node_modules" not in patch


def test_the_copy_is_kept_when_the_patch_cannot_be_made(
    repo: Path, fake_claude, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_claude("echo two >> a.txt")

    def broken(*_: object) -> str:
        raise ConfigError("git add failed")

    monkeypatch.setattr(localagent, "_patch", broken)
    result = _run(repo)
    copy = state_dir() / "agents" / "run-1"
    assert result.status == "error"
    assert str(copy) in result.answer
    assert (copy / "a.txt").read_text() == "one\ntwo\n"


def test_patches_older_than_a_week_are_removed(repo: Path, fake_claude) -> None:
    folder = state_dir() / "agents"
    folder.mkdir(parents=True)
    old = folder / "old.patch"
    old.write_text("x")
    week = localagent.KEEP_PATCHES_S + 60
    os.utime(old, (old.stat().st_atime - week, old.stat().st_mtime - week))
    fresh = folder / "fresh.patch"
    fresh.write_text("x")
    fake_claude("true")
    _run(repo)
    assert not old.exists()
    assert fresh.exists()
