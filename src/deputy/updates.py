"""Find out whether a newer release exists, and upgrade when asked. No telemetry: the only request
is a GET of the public "latest release" of the repository, at most once a day, and it can be off."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from deputy.config import state_dir
from deputy.version import current_version, is_compatible, is_newer

REPO = "tommasomarchionni/deputy"
RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
WHEEL_URL = (
    "https://github.com/{repo}/releases/download/v{version}/deputy-{version}-py3-none-any.whl"
)
SUCCESS_TTL_S = 24 * 3600
FAILURE_TTL_S = 3600


class UpdateError(Exception):
    """The upgrade could not be done. The message tells the user what to do instead."""


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str | None

    @property
    def available(self) -> bool:
        return self.latest is not None and is_newer(self.latest, self.current)

    @property
    def compatible(self) -> bool:
        """A newer version that may be applied without asking (see `is_compatible`)."""
        return (
            self.available and self.latest is not None and is_compatible(self.latest, self.current)
        )


def _cache_file() -> Path:
    return state_dir() / "update-check.json"


def _read_cache(now: float) -> tuple[bool, str | None]:
    """(is_fresh, latest). A stale or unreadable cache is not fresh."""
    try:
        data = json.loads(_cache_file().read_text(encoding="utf-8"))
        ttl = SUCCESS_TTL_S if data.get("ok") else FAILURE_TTL_S
        return now - float(data["checked_at"]) < ttl, data.get("latest")
    except (OSError, ValueError, KeyError, TypeError):
        return False, None


def _write_cache(now: float, latest: str | None) -> None:
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        payload = {"checked_at": now, "latest": latest, "ok": latest is not None}
        _cache_file().write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        return


def _fetch_latest(client: httpx.Client) -> str | None:
    try:
        response = client.get(
            RELEASE_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": "deputy"}
        )
        response.raise_for_status()
        tag = str(response.json()["tag_name"])
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    return tag.removeprefix("v")


def check(
    *,
    force: bool = False,
    client: httpx.Client | None = None,
    now: Callable[[], float] = time.time,
) -> UpdateInfo:
    """The installed and the latest released version, from a daily cache unless `force`."""
    installed = current_version()
    moment = now()
    fresh, cached = _read_cache(moment)
    if fresh and not force:
        return UpdateInfo(installed, cached)
    owned = client is None
    http = client or httpx.Client(timeout=3.0)
    try:
        latest = _fetch_latest(http)
    finally:
        if owned:
            http.close()
    _write_cache(moment, latest or cached)
    return UpdateInfo(installed, latest or cached)


Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603


def upgrade(latest: str, runner: Runner = _run) -> str:
    """Install the released wheel of `latest` with uv. Returns a one-line report."""
    uv = shutil.which("uv")
    if uv is None:
        raise UpdateError("uv is not on PATH; run the installer again (see docs/install.md)")
    wheel = WHEEL_URL.format(repo=REPO, version=latest)
    done = runner([uv, "tool", "install", "--force", wheel])
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()[-1:]
        raise UpdateError(f"uv failed: {detail[0] if detail else 'unknown error'}")
    return f"updated to {latest}; the new version is used from the next start"
