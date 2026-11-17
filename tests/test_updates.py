# pyright: basic
import json
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from deputy import cli, updates
from deputy.cli import update as update_cli
from deputy.config import config_path, state_dir
from deputy.version import is_compatible, is_newer, parse_version


def release_client(tag: str | None, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        return httpx.Response(status, json={"tag_name": tag} if tag else {})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "current_version", lambda: "0.1.0")


@pytest.mark.parametrize(
    ("text", "expected"),
    [("v1.2.3", (1, 2, 3)), ("0.1.0", (0, 1, 0)), ("2.0.0rc1", (2, 0, 0)), ("junk", (0,))],
)
def test_versions_are_parsed(text: str, expected: tuple[int, ...]) -> None:
    assert parse_version(text) == expected


def test_versions_compare_numerically_not_alphabetically() -> None:
    assert is_newer("0.10.0", "0.9.0")
    assert not is_newer("0.1.0", "0.1.0")
    assert is_compatible("1.4.0", "1.0.0")
    assert not is_compatible("2.0.0", "1.9.0")


def test_a_newer_release_is_reported_and_cached() -> None:
    info = updates.check(client=release_client("v0.2.0"))
    assert info.available
    assert info.compatible is False  # in 0.x a new minor may break: ask first
    cached = updates.check(client=release_client(None, 500))  # the network is not used again
    assert cached.latest == "0.2.0"


def test_a_stale_cache_is_refreshed() -> None:
    updates.check(client=release_client("0.2.0"), now=lambda: 1_000.0)
    later = 1_000.0 + updates.SUCCESS_TTL_S + 1
    assert updates.check(client=release_client("0.3.0"), now=lambda: later).latest == "0.3.0"


def test_a_network_failure_is_quiet_and_throttled() -> None:
    info = updates.check(client=release_client(None, 500))
    assert info.latest is None
    assert not info.available
    data = json.loads((state_dir() / "update-check.json").read_text())
    assert data["ok"] is False
    assert time.time() - data["checked_at"] < 5


def test_force_ignores_a_fresh_cache() -> None:
    updates.check(client=release_client("0.2.0"))
    assert updates.check(force=True, client=release_client("0.9.0")).latest == "0.9.0"


def test_compatible_means_is_compatible_and_newer() -> None:
    assert updates.UpdateInfo("1.0.0", "1.2.0").compatible
    assert not updates.UpdateInfo("1.0.0", "2.0.0").compatible
    assert not updates.UpdateInfo("1.0.0", "1.0.0").compatible
    assert not updates.UpdateInfo("1.0.0", None).available


def fake_run(code: int, err: str = "") -> updates.Runner:
    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        run.argv = argv  # type: ignore[attr-defined]
        return subprocess.CompletedProcess(argv, code, "", err)

    return run


def test_upgrade_reinstalls_at_the_release_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates.shutil, "which", lambda _: "/usr/bin/uv")
    runner = fake_run(0)
    assert "updated to 0.2.0" in updates.upgrade("0.2.0", runner)
    assert runner.argv[-1].endswith("@v0.2.0")  # type: ignore[attr-defined]


def test_upgrade_failures_are_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates.shutil, "which", lambda _: "/usr/bin/uv")
    with pytest.raises(updates.UpdateError, match="boom"):
        updates.upgrade("0.2.0", fake_run(1, "line\nboom"))
    monkeypatch.setattr(updates.shutil, "which", lambda _: None)
    with pytest.raises(updates.UpdateError, match="uv is not on PATH"):
        updates.upgrade("0.2.0", fake_run(0))


def configure(auto_update: str) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'default_backend = "l"\nauto_update = "{auto_update}"\n'
        '[[backends]]\nname = "l"\nbase_url = "http://x"\n'
    )


def test_apply_if_opted_in_only_acts_on_compatible_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(updates, "upgrade", lambda latest: calls.append(latest) or "done")
    configure("notify")
    assert update_cli.apply_if_opted_in() is None
    configure("apply")
    monkeypatch.setattr(updates, "check", lambda **_: updates.UpdateInfo("1.0.0", "2.0.0"))
    assert update_cli.apply_if_opted_in() is None  # a new major is never applied silently
    monkeypatch.setattr(updates, "check", lambda **_: updates.UpdateInfo("1.0.0", "1.1.0"))
    assert update_cli.apply_if_opted_in() == "done"
    assert calls == ["1.1.0"]


def test_apply_if_opted_in_reports_a_failed_upgrade_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure("apply")
    monkeypatch.setattr(updates, "check", lambda **_: updates.UpdateInfo("1.0.0", "1.1.0"))

    def failing(latest: str) -> str:
        raise updates.UpdateError("no uv")

    monkeypatch.setattr(updates, "upgrade", failing)
    assert "skipped" in (update_cli.apply_if_opted_in() or "")


def test_the_update_command_checks_and_upgrades(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(updates, "check", lambda **_: updates.UpdateInfo("0.1.0", "0.1.0"))
    assert cli.main(["update"]) == 0
    assert "up to date" in capsys.readouterr().out
    monkeypatch.setattr(updates, "check", lambda **_: updates.UpdateInfo("0.1.0", None))
    assert cli.main(["update"]) == 1
    monkeypatch.setattr(updates, "check", lambda **_: updates.UpdateInfo("0.1.0", "0.2.0"))
    assert cli.main(["update", "--check"]) == 0
    assert "0.2.0 is available" in capsys.readouterr().out
    monkeypatch.setattr(updates, "upgrade", lambda latest: f"updated to {latest}")
    assert cli.main(["update"]) == 0
    assert "updated to 0.2.0" in capsys.readouterr().out


def test_the_cache_lives_in_the_state_directory() -> None:
    updates.check(client=release_client("0.2.0"))
    assert Path(state_dir(), "update-check.json").is_file()
