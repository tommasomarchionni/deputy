# pyright: basic
"""Decide whether to cut a release now, and which version, from the commits since the last tag.

Cadence (the one that worked in voice-translations): release when 10 releasable changes have
accumulated, otherwise at most every 12 hours; on demand with `force`, which skips the thresholds
and never the safety checks (`main` must be green and there must be something to release).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass

RELEASABLE_TYPES = ("feat", "fix", "perf")
THRESHOLD = 10
MIN_HOURS = 12.0
HEADER = re.compile(r"^(?P<type>[a-z]+)(?:\([a-z-]+\))?(?P<breaking>!)?: ")


@dataclass(frozen=True)
class Commit:
    subject: str
    body: str = ""

    @property
    def kind(self) -> str | None:
        match = HEADER.match(self.subject)
        return match.group("type") if match else None

    @property
    def breaking(self) -> bool:
        match = HEADER.match(self.subject)
        return bool(match and match.group("breaking")) or "BREAKING CHANGE" in self.body

    @property
    def releasable(self) -> bool:
        return self.kind in RELEASABLE_TYPES or (self.kind is not None and self.breaking)


@dataclass(frozen=True)
class Decision:
    release: bool
    version: str
    reason: str


def next_version(last: str, commits: list[Commit]) -> str:
    """Bump from the commit types. While the major is 0, a feature or a breaking change bumps the
    minor and anything else the patch; from 1.0 on, breaking bumps the major."""
    major, minor, patch = (int(p) for p in [*last.split("."), "0", "0"][:3])
    breaking = any(c.breaking for c in commits)
    feature = any(c.kind == "feat" for c in commits)
    if major == 0:
        return f"0.{minor + 1}.0" if (breaking or feature) else f"0.{minor}.{patch + 1}"
    if breaking:
        return f"{major + 1}.0.0"
    return f"{major}.{minor + 1}.0" if feature else f"{major}.{minor}.{patch + 1}"


def decide(  # noqa: PLR0913
    *,
    commits: list[Commit],
    last_version: str,
    hours_since_last: float,
    ci_green: bool,
    event: str,
    force: bool,
) -> Decision:
    releasable = [c for c in commits if c.releasable]
    version = next_version(last_version, releasable)
    if not ci_green:
        return Decision(False, version, "main is not green (checks missing or failing)")
    if not releasable:
        return Decision(
            False, version, "nothing to release (no feat, fix or perf since the last tag)"
        )
    if force:
        return Decision(True, version, f"forced; {len(releasable)} releasable change(s)")
    if len(releasable) >= THRESHOLD:
        return Decision(
            True, version, f"{len(releasable)} releasable changes (threshold {THRESHOLD})"
        )
    if event == "schedule" and hours_since_last >= MIN_HOURS:
        return Decision(
            True, version, f"scheduled; {hours_since_last:.0f} h since the last release"
        )
    return Decision(
        False,
        version,
        f"{len(releasable)} change(s), {hours_since_last:.0f} h since the last release",
    )


def _git(*args: str) -> str:
    done = subprocess.run(["git", *args], capture_output=True, text=True, check=True)  # noqa: S603, S607
    return done.stdout.strip()


def _last_tag() -> str | None:
    try:
        return _git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")
    except subprocess.CalledProcessError:
        return None


def _commits_since(tag: str | None) -> list[Commit]:
    spec = f"{tag}..HEAD" if tag else "HEAD"
    raw = _git("log", spec, "--format=%s%x1f%b%x1e")
    items = [part.strip("\n") for part in raw.split("\x1e") if part.strip()]
    return [Commit(*[*item.split("\x1f", 1), ""][:2]) for item in items]


def _ci_green(sha: str) -> bool:
    """The `Required` aggregate check of CI succeeded on this exact commit."""
    done = subprocess.run(  # noqa: S603
        ["gh", "api", f"repos/{os.environ['GH_REPO']}/commits/{sha}/check-runs", "--paginate"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    runs = [
        r
        for page in done.stdout.replace("}{", "}\n{").splitlines()
        if page
        for r in json.loads(page)["check_runs"]
    ]
    return any(r["name"] == "Required" and r["conclusion"] == "success" for r in runs)


def main() -> int:
    tag = _last_tag()
    commits = _commits_since(tag)
    hours = (time.time() - int(_git("log", "-1", "--format=%ct", tag))) / 3600 if tag else 1e9
    decision = decide(
        commits=commits,
        last_version=(tag or "v0.0.0").removeprefix("v"),
        hours_since_last=hours,
        ci_green=_ci_green(_git("rev-parse", "HEAD")),
        event=os.environ.get("EVENT", ""),
        force=os.environ.get("FORCE", "false") == "true",
    )
    print(f"release={decision.release} version={decision.version}: {decision.reason}")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(
            f"release={'true' if decision.release else 'false'}\nversion={decision.version}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
