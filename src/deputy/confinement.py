"""Ready-made confinement for `command` checks: a wrapper that runs the command in an OS sandbox.

A `command` check may run a file the worker just wrote, so without a wrapper the worker can run
code with the user's rights. A wrapper is an argv prefix (`policy.command_wrapper`); `{workdir}` in
it is replaced by the working directory of the check.
"""

from __future__ import annotations

import shutil
import sys
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
# test suites start servers and talk to local services; nothing beyond this machine is.
_AGENT_SECRETS = (".ssh", ".aws", ".gnupg", ".kube", ".docker", ".config", ".claude", ".netrc",
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
        f'  (subpath (string-append (param "HOME") "/{name}"))\n' for name in _AGENT_SECRETS
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


def agent_wrapper(copy: Path, scratch: Path, platform: str | None = None) -> list[str] | None:
    """A sandbox for the `command` checks of a local agent's run, or None when this machine has
    no sandbox tool: writes only in the copy and its temporary folder, no reading of the home
    folder's secrets, no network beyond localhost (Linux: none at all)."""
    system = platform or sys.platform
    if system == "darwin" and shutil.which("sandbox-exec"):
        profile = copy.with_name(f"{copy.name}.checks.sb")
        profile.write_text(AGENT_MACOS_PROFILE, encoding="utf-8")
        profile.chmod(0o600)
        return [
            "sandbox-exec", "-f", str(profile), "-D", f"WORKDIR={copy.resolve()}",
            "-D", f"SCRATCH={scratch.resolve()}", "-D", f"HOME={Path.home()}",
        ]  # fmt: skip
    if system.startswith("linux") and shutil.which("bwrap"):
        both = [part.replace("{workdir}", str(copy)) for part in LINUX_WRAPPER]
        return [*both[:-2], "--bind", str(scratch), str(scratch), *both[-2:]]
    return None


def agent_wrapper_tool(platform: str | None = None) -> str | None:
    """The sandbox tool `agent_wrapper` would use on this machine, if there is one."""
    system = platform or sys.platform
    name = "sandbox-exec" if system == "darwin" else "bwrap" if system.startswith("linux") else ""
    return shutil.which(name) if name else None


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
