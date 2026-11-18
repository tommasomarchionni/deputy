"""Find out whether a newer release exists, and upgrade when asked. No telemetry: the only request
is a GET of the public "latest release" of the repository, at most once a day, and it can be off."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
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
MAX_WHEEL_BYTES = 50_000_000
DOWNLOAD_TIMEOUT_S = 60.0


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603


class Provenance(Enum):
    VERIFIED = "verified"  # the attestation matches this repository's release workflow
    UNAVAILABLE = "unavailable"  # could not be checked (no gh, or not logged in)
    FAILED = "failed"  # checked, and it did not match


@dataclass(frozen=True)
class Verification:
    outcome: Provenance
    detail: str = ""


def verify_attestation(wheel: Path, runner: Runner = _run) -> Verification:
    """Check the build provenance of a downloaded wheel with `gh attestation verify`.

    The attestation is made by the release workflow of this repository; verifying it proves the
    wheel was built there from a tagged commit and not swapped afterwards."""
    gh = shutil.which("gh")
    if gh is None:
        return Verification(Provenance.UNAVAILABLE, "the GitHub CLI (gh) is not installed")
    if runner([gh, "auth", "status"]).returncode != 0:
        return Verification(Provenance.UNAVAILABLE, "gh is not logged in (run: gh auth login)")
    done = runner([gh, "attestation", "verify", str(wheel), "--repo", REPO])
    if done.returncode == 0:
        return Verification(Provenance.VERIFIED)
    lines = (done.stderr or done.stdout).strip().splitlines()
    return Verification(Provenance.FAILED, lines[-1] if lines else "verification failed")


def _download(url: str, target: Path, client: httpx.Client | None) -> None:
    owned = client is None
    http = client or httpx.Client(timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True)
    try:
        response = http.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise UpdateError(f"could not download the release ({type(exc).__name__})") from exc
    finally:
        if owned:
            http.close()
    if len(response.content) > MAX_WHEEL_BYTES:
        raise UpdateError("the downloaded file is much larger than a deputy release; refusing it")
    target.write_bytes(response.content)


def upgrade(
    latest: str,
    runner: Runner = _run,
    *,
    require_verified: bool = False,
    client: httpx.Client | None = None,
) -> str:
    """Install the released wheel of `latest` with uv, after checking where it was built.

    The wheel is downloaded, its attestation verified when it can be, and only then installed. A
    failed verification always refuses. If it cannot be done (no `gh`, not logged in) the update
    goes ahead with a warning, unless `require_verified` is set (an automatic update sets it)."""
    uv = shutil.which("uv")
    if uv is None:
        raise UpdateError("uv is not on PATH; run the installer again (see docs/install.md)")
    name = f"deputy-{latest}-py3-none-any.whl"
    with tempfile.TemporaryDirectory(prefix="deputy-update-") as folder:
        wheel = Path(folder) / name
        _download(WHEEL_URL.format(repo=REPO, version=latest), wheel, client)
        check = verify_attestation(wheel, runner)
        if check.outcome is Provenance.FAILED:
            raise UpdateError(
                f"the release failed its provenance check, not installed: {check.detail}"
            )
        if check.outcome is Provenance.UNAVAILABLE and require_verified:
            raise UpdateError(f"provenance could not be checked ({check.detail}); not installed")
        done = runner([uv, "tool", "install", "--force", str(wheel)])
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()[-1:]
        raise UpdateError(f"uv failed: {detail[0] if detail else 'unknown error'}")
    note = (
        "provenance verified"
        if check.outcome is Provenance.VERIFIED
        else f"provenance NOT verified: {check.detail}"
    )
    return f"updated to {latest} ({note}); the new version is used from the next start"
