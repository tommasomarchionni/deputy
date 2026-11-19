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
from deputy.checks import CheckResult, Command, Contains
from deputy.config import Backend, ConfigError, Policy, state_dir
from deputy.gateway import MAX_TOKENS

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

    def install(
        shell: str,
        subtype: str = "success",
        summary: str = "added the file",
        repair: str = "true",
        *,
        stuck: int = 0,
    ) -> None:
        """`shell` runs for the task; `repair` for each resumed round. The first `stuck` rounds
        only read, with no result, and would hang if deputy did not stop them."""
        event = json.dumps(
            {"type": "result", "subtype": subtype, "result": summary, "session_id": "s1"}
        )
        tool = json.dumps(
            {"message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash"}]}}
        )
        program = folder / "claude"
        read = json.dumps(
            {"message": {"content": [{"type": "tool_use", "id": "r", "name": "Read"}]}}
        )
        stalled = (
            f'if [ "$(grep -c x {tmp_path}/rounds)" -le {stuck} ]; then '
            'echo \'{"type": "system", "session_id": "s1"}\'; '
            f"for i in $(seq {localagent.STALL_CALLS}); do echo '{read}'; done; sleep 30; fi\n"
        )
        program.write_text(
            f"#!/bin/sh\necho x >> {tmp_path}/rounds\n{stalled if stuck else ''}"
            f'case " $* " in *" --resume s1 "*) {repair};; *) {shell};; esac\n'
            f"echo '{tool}'\necho '{event}'\n"
        )
        program.chmod(program.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(localagent, "Gateway", FakeGateway)

    async def context(_: Backend) -> int:
        return 131_072

    monkeypatch.setattr(localagent, "require_context", context)
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


def _rounds(tmp_path: Path) -> int:
    return (tmp_path / "rounds").read_text().count("x")


def test_failed_checks_go_back_to_the_agent(repo: Path, fake_claude, tmp_path: Path) -> None:
    fake_claude("echo bad > out.txt", repair="echo good > out.txt")
    check = Command(argv=["grep", "-q", "good", "out.txt"])
    result = asyncio.run(
        localagent.run(
            "do it", repo, BACKEND, [check], Policy(allowed_commands=[["grep"]]), "run-1"
        )
    )
    assert result.status == "ok"
    assert _rounds(tmp_path) == 2
    assert "After 1 repair round(s)" in result.answer
    assert "+good" in (state_dir() / "agents" / "run-1.patch").read_text()


def test_repairs_stop_after_a_few_rounds(repo: Path, fake_claude, tmp_path: Path) -> None:
    fake_claude("echo two >> a.txt")
    result = _run(repo, [Contains(values=["never said"])])
    assert result.status == "checks_failed"
    assert _rounds(tmp_path) == 1 + localagent.REPAIR_ROUNDS


def test_a_stuck_agent_is_stopped_and_told_to_move_on(
    repo: Path, fake_claude, tmp_path: Path
) -> None:
    fake_claude("true", repair="echo done > done.txt", stuck=1)
    result = _run(repo)
    assert result.status == "ok"
    assert _rounds(tmp_path) == 2  # stopped once, then resumed with the nudge
    assert "done.txt" in (state_dir() / "agents" / "run-1.patch").read_text()


def test_an_agent_that_stays_stuck_hands_the_task_back(
    repo: Path, fake_claude, tmp_path: Path
) -> None:
    fake_claude("true", stuck=1 + localagent.NUDGES)
    result = _run(repo)
    assert result.status == "error"
    assert result.flags == ["stuck", "needs_frontier"]
    assert _rounds(tmp_path) == 1 + localagent.NUDGES


_LAST = "for word; do prompt=$word; done; "  # the prompt is the last argument of `claude`


def _stall() -> str:
    """Shell for a fake agent that only reads and never ends: deputy has to stop it."""
    read = json.dumps({"message": {"content": [{"type": "tool_use", "id": "r", "name": "Read"}]}})
    return (
        'echo \'{"type": "system", "session_id": "s1"}\'; '
        f"for i in $(seq {localagent.STALL_CALLS}); do echo '{read}'; done; sleep 30"
    )


def _run_steps(repo: Path, steps: list[str], checks: list | None = None, policy=None):
    return asyncio.run(
        localagent.run(
            "shared rules", repo, BACKEND, checks or [], policy or Policy(), "run-1", steps
        )
    )


def test_each_step_gets_a_conversation_of_its_own(repo: Path, fake_claude, tmp_path: Path) -> None:
    log = tmp_path / "prompts"
    fake_claude(f'{_LAST}echo "$prompt" | grep -o "You do step [0-9]*" >> {log}; echo x >> a.txt')
    result = _run_steps(repo, ["first change", "second change"])
    assert result.status == "ok"
    assert log.read_text().splitlines() == ["You do step 1", "You do step 2"]
    assert _rounds(tmp_path) == 2
    assert result.answer.startswith("1. added the file\n2. added the file")
    assert "+x\n+x" in (state_dir() / "agents" / "run-1.patch").read_text()


def test_a_step_prompt_holds_the_shared_task_and_only_its_step() -> None:
    prompt = localagent.step_prompt("shared rules", ["first change", "second change"], 1, [])
    assert prompt.startswith("shared rules\n\n")
    assert "Step 2: second change" in prompt
    assert "first change" not in prompt


def test_a_step_the_agent_cannot_do_is_undone_and_named(
    repo: Path, fake_claude, tmp_path: Path
) -> None:
    stuck = f"echo half > half.txt; echo broken >> a.txt; {_stall()}"
    work = f'{_LAST}case "$prompt" in *"You do step 2"*) {stuck};; *) echo ok >> a.txt;; esac'
    fake_claude(work, repair=_stall())  # the nudges resume the conversation, still stuck
    result = _run_steps(repo, ["one", "two", "three"])
    assert result.status == "error"
    assert result.flags == ["stuck", "partial", "needs_frontier"]
    assert "2. NOT DONE (stuck)" in result.answer
    assert "Left for you: step 2." in result.answer
    patch = (state_dir() / "agents" / "run-1.patch").read_text()
    assert "half.txt" not in patch
    assert "broken" not in patch
    assert "+ok\n+ok" in patch  # steps one and three are in the patch
    assert _rounds(tmp_path) == 3 + localagent.NUDGES


def test_failed_checks_of_steps_go_to_a_new_conversation_that_knows_the_steps(
    repo: Path, fake_claude, tmp_path: Path
) -> None:
    log = tmp_path / "repairs"
    repair = f'echo "$prompt" > {log}; echo good > out.txt'
    fake_claude(f'{_LAST}case "$prompt" in *"these fail"*) {repair};; *) echo bad > out.txt;; esac')
    check = Command(argv=["grep", "-q", "good", "out.txt"])
    result = _run_steps(repo, ["one", "two"], [check], Policy(allowed_commands=[["grep"]]))
    assert result.status == "ok"
    assert "After 1 repair round(s)" in result.answer
    assert "Other runs just made these changes in this copy:\n1. one\n2. two" in log.read_text()
    assert _rounds(tmp_path) == 3


def test_command_checks_test_the_copy(repo: Path, fake_claude) -> None:
    fake_claude("true")
    copy_src = state_dir() / "agents" / "run-1" / "src"
    probe = f'test "$UV_NO_SYNC" = 1 && test "$PYTHONPATH" = "{copy_src}"'
    result = asyncio.run(
        localagent.run(
            "do it",
            repo,
            BACKEND,
            [Command(argv=["sh", "-c", probe])],
            Policy(allowed_commands=[["sh"]]),
            "run-1",
        )
    )
    assert result.status == "ok", result.checks


def test_the_conversations_stay_out_of_the_user_history(repo: Path, fake_claude) -> None:
    fake_claude('mkdir -p "$CLAUDE_CONFIG_DIR/projects" && echo seen > seen.txt')
    assert _run(repo).status == "ok"
    assert "seen.txt" in (state_dir() / "agents" / "run-1.patch").read_text()
    assert not (state_dir() / "agents" / "run-1.claude").exists()


def test_an_agent_that_does_not_finish_hands_the_task_back(repo: Path, fake_claude) -> None:
    fake_claude("true", subtype="error_during_execution")
    result = _run(repo)
    assert result.status == "error"
    assert "needs_frontier" in result.flags


def test_a_folder_that_is_not_a_repository_is_refused(tmp_path: Path, fake_claude) -> None:
    fake_claude("true")
    with pytest.raises(ConfigError, match="git rev-parse failed"):
        _run(tmp_path)


def test_temporary_files_are_outside_the_copy_and_any_repository(repo: Path, fake_claude) -> None:
    # a test's temporary folder inside the copy is inside a repository: tests that create a
    # folder outside every repository then fail, and the agent was led to "fix" correct code
    fake_claude('cd "$TMPDIR" && ! git rev-parse 2>/dev/null && touch made && cd - && echo ok > ok')
    assert _run(repo).status == "ok"
    assert "ok" in (state_dir() / "agents" / "run-1.patch").read_text()
    assert not (state_dir() / "agents" / "run-1.tmp").exists()


def test_the_agent_is_told_which_commands_will_check_its_work() -> None:
    gates = [Command(argv=["uv", "run", "pytest", "-q"]), Contains(values=["x"])]
    prompt = localagent.task_prompt("do it", gates)
    assert prompt.startswith("do it\n\n")
    assert "`uv run pytest -q`" in prompt
    assert "only the tests of the code you change" in prompt
    assert "even if the task asks for it" in prompt
    assert localagent.task_prompt("do it", [Contains(values=["x"])]) == "do it"


def test_the_repair_prompt_forbids_working_around_the_environment() -> None:
    failed = CheckResult(name="command: pytest", passed=False, detail="1 failed")
    prompt = localagent.repair_prompt([failed, CheckResult(name="other", passed=True)])
    assert "command: pytest: 1 failed" in prompt
    assert "other" not in prompt
    assert "from the environment" in prompt
    assert "Never weaken" in prompt


def test_generated_files_are_not_part_of_the_patch(repo: Path, fake_claude) -> None:
    fake_claude(
        "mkdir __pycache__ .tmp; echo x > __pycache__/a.pyc; echo t > .tmp/t; echo two >> a.txt"
    )
    assert _run(repo).status == "ok"
    patch = (state_dir() / "agents" / "run-1.patch").read_text()
    assert "a.txt" in patch
    assert "pyc" not in patch
    assert ".tmp" not in patch


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
    assert sandbox["filesystem"]["allowWrite"] == [f"/{tmp_path.resolve()}.tmp"]
    assert localagent.agent_settings(tmp_path)["disableAllHooks"] is True


def test_the_agent_gets_no_credential_of_the_user(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "secret")
    monkeypatch.setenv("VIRTUAL_ENV", "/deputy/.venv")
    env = localagent.agent_environment(cast("Any", FakeGateway(BACKEND)), BACKEND, Path("/copy"))
    assert "secret" not in env.values()
    assert "VIRTUAL_ENV" not in env
    assert env["ANTHROPIC_AUTH_TOKEN"] == "gateway-token"
    assert env["ANTHROPIC_BASE_URL"] == FakeGateway.url
    assert env["PYTHONPATH"] == "/copy/src"
    assert env["TMPDIR"] == "/copy.tmp"
    assert env["CLAUDE_CONFIG_DIR"] == "/copy.claude"


def test_the_agent_compacts_late_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000")
    env = localagent.agent_environment(cast("Any", FakeGateway(BACKEND)), BACKEND, Path("/copy"))
    # Claude Code compacts at the window minus room for one reply: the gateway's cap, not 20,000.
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == str(MAX_TOKENS)
    assert int(env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"]) < localagent.CONTEXT_NEEDED


def test_the_agent_compacts_as_late_as_the_server_allows() -> None:
    assert localagent.compact_window(131_072) == 121_072
    assert localagent.compact_window(0) == localagent.SMALLEST_WINDOW  # the server did not say
    assert localagent.compact_window(110_000) == localagent.SMALLEST_WINDOW
    gateway = cast("Any", FakeGateway(BACKEND))
    env = localagent.agent_environment(gateway, BACKEND, Path("/copy"), 121_072)
    assert env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "121072"


def test_a_command_that_does_not_end_is_stopped_not_sent_to_the_background() -> None:
    env = localagent.agent_environment(cast("Any", FakeGateway(BACKEND)), BACKEND, Path("/copy"))
    assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    assert int(env["BASH_MAX_TIMEOUT_MS"]) == localagent.COMMAND_LIMIT_S * 1000


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
    expected = 131_072 if reply.status_code == 200 else 0
    assert asyncio.run(localagent.require_context(BACKEND)) == expected


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
