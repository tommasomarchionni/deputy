# pyright: basic
import asyncio
from pathlib import Path

import pytest

from deputy import taskfile
from deputy.config import Backend, ConfigError, Policy
from helpers import message, tool_call

BACKEND = Backend(name="fake", base_url="http://fake", model="fake-model")


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "tasks.toml"
    path.write_text(text)
    return path


def _one(tmp_path: Path, name: str, task: str, check: str) -> Path:
    return _write(
        tmp_path, f'[[task]]\nname = "{name}"\ntask = "{task}"\nchecks = [{{ {check} }}]\n'
    )


CONTAINS_BETA = 'type = "contains", values = ["beta"]'


def test_a_valid_file_gives_tasks_with_validated_checks(tmp_path: Path) -> None:
    path = _one(tmp_path, "a", "find beta", CONTAINS_BETA)
    [task] = taskfile.load_tasks(path)
    assert task.name == "a"
    assert len(task.checks) == 1


@pytest.mark.parametrize(
    "text",
    [
        "",
        '[[task]]\nname = "a"\n',
        '[[task]]\nname = "a"\ntask = "x"\nchecks = [{ type = "cites_lines" }]\n',
        '[[task]]\nname = "a"\ntask = "x"\nchecks = [{ type = "nonsense" }]\n',
        "not toml [",
    ],
)
def test_a_broken_file_is_a_clear_config_error(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError):
        taskfile.load_tasks(_write(tmp_path, text))


def test_a_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        taskfile.load_tasks(tmp_path / "missing.toml")


def _run(tmp_path: Path, fake_client, script, tasks):  # type: ignore[no-untyped-def]
    return asyncio.run(
        taskfile.run_tasks(
            tasks, tmp_path, BACKEND, policy=Policy(), client_factory=lambda _b: fake_client(script)
        )
    )


def test_each_task_is_graded_by_its_checks(tmp_path: Path, fake_client) -> None:  # type: ignore[no-untyped-def]
    path = _one(tmp_path, "a", "say beta", CONTAINS_BETA)
    [row] = _run(tmp_path, fake_client, [message("beta")], taskfile.load_tasks(path))
    assert row.passed
    assert row.status == "ok"


def test_a_failing_check_is_reported_not_hidden(tmp_path: Path, fake_client) -> None:  # type: ignore[no-untyped-def]
    path = _one(tmp_path, "a", "say beta", CONTAINS_BETA)
    [row] = _run(tmp_path, fake_client, [message("alpha")], taskfile.load_tasks(path))
    assert not row.passed
    assert row.status == "checks_failed"


def test_what_the_worker_writes_is_rolled_back_after_the_task(tmp_path: Path, fake_client) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "keep.txt").write_text("original")
    path = _one(tmp_path, "w", "write out.txt", 'type = "file_written", path = "out.txt"')
    script = [
        message(tool_calls=[tool_call("write_file", {"path": "out.txt", "content": "hi"})]),
        message("done"),
    ]
    [row] = _run(tmp_path, fake_client, script, taskfile.load_tasks(path))
    assert row.passed
    assert not (tmp_path / "out.txt").exists()
    assert (tmp_path / "keep.txt").read_text() == "original"


def test_the_table_shows_each_task_and_a_total(tmp_path: Path) -> None:
    rows = [
        taskfile.Row("long-read", True, "ok", 3, 120.5, 4000),
        taskfile.Row("tiny", True, "ok", 2, 10.0, -74),
    ]
    text = taskfile.format_rows(rows)
    assert "long-read" in text
    assert "-74" in text
    assert "2/2 ok" in text
    assert "3926" in text
