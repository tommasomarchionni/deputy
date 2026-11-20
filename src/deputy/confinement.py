"""Ready-made confinement for `command` checks: a wrapper that runs the command in an OS sandbox.

A `command` check may run a file the worker just wrote, so without a wrapper the worker can run
code with the user's rights. A wrapper is an argv prefix (`policy.command_wrapper`); `{workdir}` in
it is replaced by the working directory of the check.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from deputy.config import config_dir

MACOS_PROFILE = """\
(version 1)
(deny default)
(allow process-exec* process-fork signal sysctl-read mach-lookup ipc-posix-shm)
(allow file-read*)
(deny file-read*
  (subpath (string-append (param "HOME") "/.ssh"))
  (subpath (string-append (param "HOME") "/.aws"))
  (subpath (string-append (param "HOME") "/.gnupg"))
  (subpath (string-append (param "HOME") "/.config")))
(allow file-write*
  (subpath (param "WORKDIR"))
  (subpath "/private/tmp")
  (subpath "/private/var/folders")
  (literal "/dev/null"))
(deny network*)
"""

# For the checks of the local agent: the same idea, on its copy. Localhost stays reachable, because
# test suites start servers and talk to local services; nothing beyond this machine is, and not
# the system's name lookups either. Unix sockets work where files can be written (Nx needs one).
_PRIVATE_PLACES = (".ssh", ".aws", ".gnupg", ".kube", ".docker", ".config", ".claude", ".netrc",
                  ".npmrc", ".pypirc", ".git-credentials")  # fmt: skip
AGENT_MACOS_PROFILE = (
    """\
(version 1)
(deny default)
(allow process-exec* process-fork signal sysctl-read mach-lookup ipc-posix-shm)
(allow file-read*)
(deny file-read*
"""
    + "".join(
        f'  (subpath (string-append (param "HOME") "/{name}"))\n' for name in _PRIVATE_PLACES
    ).rstrip("\n")
    + """)
(allow file-write*
  (subpath (param "WORKDIR"))
  (subpath (param "SCRATCH"))
  (subpath "/private/tmp")
  (subpath "/private/var/folders")
  (literal "/dev/null"))
(deny network*)
(allow network-outbound (remote ip "localhost:*"))
(allow network-bind (local ip "localhost:*"))
(allow network-inbound (local ip "localhost:*"))
(allow network-outbound network-bind network-inbound
  (subpath (param "WORKDIR"))
  (subpath (param "SCRATCH"))
  (subpath "/private/tmp")
  (subpath "/private/var/folders"))
"""
)

LINUX_WRAPPER = [
    "bwrap", "--ro-bind", "/", "/", "--bind", "{workdir}", "{workdir}", "--dev", "/dev",
    "--proc", "/proc", "--tmpfs", "/tmp", "--unshare-net", "--die-with-parent",  # noqa: S108
    "--chdir", "{workdir}",
]  # fmt: skip


def macos_profile_path() -> Path:
    return config_dir() / "command-sandbox.sb"


def suggested_wrapper(platform: str | None = None) -> list[str] | None:
    """The wrapper for this platform, or None when there is none (Windows, or the tool is missing).

    macOS uses `sandbox-exec` with the profile above: writes only in the working directory (and
    temporary folders), no network, no reading of ~/.ssh, ~/.aws, ~/.gnupg or ~/.config. Linux uses
    bubblewrap: a read-only view of the system, writes only in the working directory, no network."""
    system = platform or sys.platform
    if system == "darwin" and shutil.which("sandbox-exec"):
        return [
            "sandbox-exec", "-f", str(macos_profile_path()),
            "-D", "WORKDIR={workdir}", "-D", f"HOME={Path.home()}",
        ]  # fmt: skip
    if system.startswith("linux") and shutil.which("bwrap"):
        return list(LINUX_WRAPPER)
    return None


def macos_agent_wrapper(copy: Path, scratch: Path) -> list[str]:
    profile = copy.with_name(f"{copy.name}.checks.sb")
    profile.write_text(AGENT_MACOS_PROFILE, encoding="utf-8")
    profile.chmod(0o600)
    return [
        "sandbox-exec", "-f", str(profile), "-D", f"WORKDIR={copy.resolve()}",
        "-D", f"SCRATCH={scratch.resolve()}", "-D", f"HOME={Path.home()}",
    ]  # fmt: skip


def linux_agent_wrapper(copy: Path, scratch: Path) -> list[str]:
    """bubblewrap: a read-only system with the home folder's private places hidden, an empty
    /tmp, the copy and its temporary folder writable (bound last, so they show even when they
    live under /tmp), and no network."""
    hidden: list[str] = []
    for name in _PRIVATE_PLACES:
        place = Path.home() / name
        if place.is_dir():
            hidden += ["--tmpfs", str(place)]
        elif place.is_file():
            hidden += ["--ro-bind", "/dev/null", str(place)]
    return [
        "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
        "--tmpfs", "/tmp", *hidden,  # noqa: S108
        "--bind", str(copy), str(copy), "--bind", str(scratch), str(scratch),
        "--unshare-net", "--die-with-parent", "--chdir", str(copy),
    ]  # fmt: skip


def _starts(wrapper: list[str]) -> bool:
    """A sandbox tool can be installed and still be refused by the system (bubblewrap where
    user namespaces are off): only a wrapper that runs a command is one to rely on."""
    try:
        done = subprocess.run(  # noqa: S603  (argv built here)
            [*wrapper, "true"], capture_output=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def agent_wrapper(copy: Path, scratch: Path, platform: str | None = None) -> list[str] | None:
    """A sandbox for the `command` checks of a local agent's run, or None when this machine has
    no sandbox that works: writes only in the copy and its temporary folder, no reading of the
    home folder's private places, no network beyond localhost (Linux: none at all)."""
    system = platform or sys.platform
    if system == "darwin" and shutil.which("sandbox-exec"):
        wrapper = macos_agent_wrapper(copy, scratch)
    elif system.startswith("linux") and shutil.which("bwrap"):
        wrapper = linux_agent_wrapper(copy, scratch)
    else:
        return None
    return wrapper if _starts(wrapper) else None


@functools.cache
def agent_sandbox_works() -> bool:
    """Whether `agent_wrapper` gives a sandbox on this machine: tried once, on empty folders."""
    with tempfile.TemporaryDirectory() as folder:
        copy, scratch = Path(folder) / "copy", Path(folder) / "copy.tmp"
        copy.mkdir()
        scratch.mkdir()
        return agent_wrapper(copy, scratch) is not None


def write_profile() -> Path:
    """Write the macOS sandbox profile next to the configuration, readable only by the user."""
    path = macos_profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(MACOS_PROFILE, encoding="utf-8")
    path.chmod(0o600)
    return path


def wrapper_tool(wrapper: list[str]) -> str | None:
    """The program a wrapper starts, if it can be found on this machine."""
    return shutil.which(wrapper[0]) if wrapper else None
