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


class FakeMachine:
    """Stand-in for the tools the update runs: records every command, answers from a script."""

    def __init__(
        self, *, auth: int = 0, verify: int = 0, install: int = 0, verify_err: str = ""
    ) -> None:
        self.codes = {"auth": auth, "attestation": verify, "install": install}
        self.verify_err = verify_err
        self.commands: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        key = "auth" if "auth" in argv else "attestation" if "attestation" in argv else "install"
        err = self.verify_err if key == "attestation" else ("line\nboom" if self.codes[key] else "")
        return subprocess.CompletedProcess(argv, self.codes[key], "", err)

    def installed(self) -> list[str] | None:
        return next((c for c in self.commands if "install" in c), None)


def release_download(content: bytes = b"wheel bytes", status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/download/v0.2.0/deputy-0.2.0-py3-none-any.whl")
        return httpx.Response(status, content=content)

    return httpx.Client(transport=httpx.MockTransport(handler))


def tools(monkeypatch: pytest.MonkeyPatch, *, gh: bool = True) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda name: f"/usr/bin/{name}" if name != "gh" or gh else None
    )


def test_a_verified_release_is_downloaded_checked_then_installed_from_the_local_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools(monkeypatch)
    machine = FakeMachine()
    report = updates.upgrade("0.2.0", machine, client=release_download())
    assert "updated to 0.2.0 (provenance verified)" in report
    verify = next(c for c in machine.commands if "attestation" in c)
    assert verify[-2:] == ["--repo", updates.REPO]
    install = machine.installed()
    assert install is not None
    assert install[-1].endswith("deputy-0.2.0-py3-none-any.whl")
    assert machine.commands.index(verify) < machine.commands.index(install)  # check, then install


def test_a_release_that_fails_its_provenance_check_is_never_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools(monkeypatch)
    machine = FakeMachine(verify=1, verify_err="verification failed: no matching attestation")
    with pytest.raises(updates.UpdateError, match=r"failed its provenance check.*no matching"):
        updates.upgrade("0.2.0", machine, client=release_download())
    assert machine.installed() is None


@pytest.mark.parametrize(
    ("gh_installed", "auth", "reason"),
    [(False, 0, "not installed"), (True, 1, "not logged in")],
)
def test_without_a_way_to_verify_a_manual_update_goes_ahead_and_says_so(
    monkeypatch: pytest.MonkeyPatch, gh_installed: bool, auth: int, reason: str
) -> None:
    tools(monkeypatch, gh=gh_installed)
    machine = FakeMachine(auth=auth)
    report = updates.upgrade("0.2.0", machine, client=release_download())
    assert "provenance NOT verified" in report
    assert reason in report
    assert machine.installed() is not None


@pytest.mark.parametrize("gh_installed", [False, True])
def test_an_update_that_requires_verification_refuses_when_it_cannot_verify(
    monkeypatch: pytest.MonkeyPatch, gh_installed: bool
) -> None:
    tools(monkeypatch, gh=gh_installed)
    machine = FakeMachine(auth=1)
    with pytest.raises(updates.UpdateError, match="could not be checked"):
        updates.upgrade("0.2.0", machine, require_verified=True, client=release_download())
    assert machine.installed() is None


def test_download_problems_are_explained_and_nothing_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools(monkeypatch)
    machine = FakeMachine()
    with pytest.raises(updates.UpdateError, match="could not download"):
        updates.upgrade("0.2.0", machine, client=release_download(status=404))
    monkeypatch.setattr(updates, "MAX_WHEEL_BYTES", 4)
    with pytest.raises(updates.UpdateError, match="much larger"):
        updates.upgrade("0.2.0", machine, client=release_download(b"too many bytes"))
    assert machine.installed() is None


def test_uv_failures_are_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    tools(monkeypatch)
    with pytest.raises(updates.UpdateError, match="boom"):
        updates.upgrade("0.2.0", FakeMachine(install=1), client=release_download())
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(updates.UpdateError, match="uv is not on PATH"):
        updates.upgrade("0.2.0", FakeMachine(), client=release_download())


def configure(auto_update: str) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'default_backend = "l"\nauto_update = "{auto_update}"\n'
        '[[backends]]\nname = "l"\nbase_url = "http://x"\n'
    )


def test_apply_if_opted_in_only_acts_on_compatible_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        updates,
        "upgrade",
        lambda latest, **kw: calls.append((latest, kw["require_verified"])) or "done",
    )
    configure("notify")
    assert update_cli.apply_if_opted_in() is None
    configure("apply")
    monkeypatch.setattr(updates, "check", lambda **_: updates.UpdateInfo("1.0.0", "2.0.0"))
    assert update_cli.apply_if_opted_in() is None  # a new major is never applied silently
    monkeypatch.setattr(updates, "check", lambda **_: updates.UpdateInfo("1.0.0", "1.1.0"))
    assert update_cli.apply_if_opted_in() == "done"
    assert calls == [("1.1.0", True)]  # an automatic update installs only what it could verify


def test_apply_if_opted_in_reports_a_failed_upgrade_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure("apply")
    monkeypatch.setattr(updates, "check", lambda **_: updates.UpdateInfo("1.0.0", "1.1.0"))

    def failing(latest: str, **_: object) -> str:
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
    seen: list[bool] = []
    monkeypatch.setattr(
        updates,
        "upgrade",
        lambda latest, **kw: seen.append(kw["require_verified"]) or f"updated to {latest}",
    )
    assert cli.main(["update"]) == 0
    assert "updated to 0.2.0" in capsys.readouterr().out
    assert cli.main(["update", "--require-verified"]) == 0
    assert seen == [False, True]


def test_the_cache_lives_in_the_state_directory() -> None:
    updates.check(client=release_client("0.2.0"))
    assert Path(state_dir(), "update-check.json").is_file()
