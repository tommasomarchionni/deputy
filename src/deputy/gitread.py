"""Read-only git for the worker: log, show, diff and blame, nothing else.

The argv is built here from an allow-list, never passed to a shell. Every option must be on the
list for its subcommand; anything else (writing, running a program, reading a file outside the
repository, reading a blob by `rev:path`) is refused. Helpers git could start on its own are off:
external diff, textconv, pager, fsmonitor, hooks, and system or global config. Secret-looking files
are excluded from every command by pathspec, so their content never appears in a diff.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

GIT_TIMEOUT_S = 10.0
MAX_OUTPUT_CHARS = 60_000

_COMMON = frozenset({"--stat", "--name-only", "--name-status", "--oneline", "-p", "--patch"})
_ALLOWED: dict[str, frozenset[str]] = {
    "log": _COMMON | {"--follow", "--no-merges", "--reverse", "--format", "--since", "--until",
                      "--author", "--grep", "-n", "--max-count", "-i"},
    "show": _COMMON | {"--format"},
    "diff": _COMMON | {"--cached", "-U", "--unified", "-w", "--ignore-all-space"},
    "blame": frozenset({"-L", "-w", "-s"}),
}  # fmt: skip
_TAKES_VALUE = frozenset({"-n", "-U", "-L"})  # may be written as "-n 5" or "-n5"
_REVISION = re.compile(r"^[A-Za-z0-9_./~^@{}-]+$")
_SECRET_GLOBS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "id_rsa*", "id_ed25519*",
    "id_ecdsa*", "id_dsa*", ".npmrc", ".netrc", ".pypirc", ".htpasswd", ".git-credentials",
    "credentials", "credentials.*", "secret.*", "secrets.*",
)  # fmt: skip
_SECRET_FOLDERS = (".ssh", ".aws", ".gnupg", ".kube", ".docker")
_SAFE = (
    "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
    "-c", "diff.external=", "-c", "color.ui=false",
)  # fmt: skip


class GitRefused(Exception):
    """A request the read-only git tool will not run; the message is safe to show the model."""


def _option_name(arg: str) -> str:
    if arg.startswith("--"):
        return arg.split("=", 1)[0]
    return arg[:2]  # "-n5" -> "-n", "-U3" -> "-U"


def _excludes() -> list[str]:
    files = [f":(exclude,glob,icase)**/{g}" for g in _SECRET_GLOBS]
    folders = [f":(exclude,glob,icase)**/{d}/**" for d in _SECRET_FOLDERS]
    return [*files, *folders]


def build_argv(args: list[str], *, is_inside: Callable[[str], object]) -> list[str]:
    """Check `args` against the allow-list and return the full git argv (without the binary).

    `is_inside` is a callable taking a path string and raising if the sandbox refuses it."""
    if not args or args[0] not in _ALLOWED:
        raise GitRefused(f"only {', '.join(sorted(_ALLOWED))} are allowed")
    command, rest = args[0], args[1:]
    allowed = _ALLOWED[command]
    options: list[str] = []
    revisions: list[str] = []
    paths: list[str] = []
    seen_separator = False
    pending_value = False
    for arg in rest:
        if pending_value:
            options.append(arg)
            pending_value = False
        elif seen_separator:
            is_inside(arg)
            paths.append(arg)
        elif arg == "--":
            seen_separator = True
        elif arg.startswith("-"):
            name = _option_name(arg)
            if name not in allowed:
                raise GitRefused(f"option {name} is not allowed for git {command}")
            options.append(arg)
            pending_value = name in _TAKES_VALUE and arg == name
        else:
            if ":" in arg or not _REVISION.match(arg):
                raise GitRefused(f"not a revision: {arg!r} (put paths after --)")
            revisions.append(arg)
    if pending_value:
        raise GitRefused("an option is missing its value")
    if command == "blame":
        if len(paths) != 1:
            raise GitRefused("git blame needs exactly one file after --")
        return [*_SAFE, "blame", *options, *revisions, "--", *paths]
    extra = ["--no-ext-diff", "--no-textconv", "--no-color"]
    return [*_SAFE, command, *extra, *options, *revisions, "--", *(paths or ["."]), *_excludes()]


def run(root: Path, argv: list[str]) -> str:
    """Run git in `root` with a clean environment; output capped and labelled."""
    git = shutil.which("git")
    if git is None or not (root / ".git").exists():
        raise GitRefused("this folder is not a git repository")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "LC_ALL": "C",
    }
    try:
        done = subprocess.run(  # noqa: S603  (fixed argv from the allow-list, no shell)
            [git, "-C", str(root), *argv],
            capture_output=True,
            timeout=GIT_TIMEOUT_S,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitRefused(f"git took longer than {GIT_TIMEOUT_S:g} s") from exc
    if done.returncode != 0:
        message = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitRefused(message[-1] if message else f"git exited with {done.returncode}")
    out = done.stdout.decode("utf-8", "replace")
    if len(out) > MAX_OUTPUT_CHARS:
        cut = out[:MAX_OUTPUT_CHARS].rsplit("\n", 1)[0]
        return cut + f"\n[cut at {MAX_OUTPUT_CHARS} characters: narrow it (--stat, a path, -n)]"
    return out or "(no output)"
