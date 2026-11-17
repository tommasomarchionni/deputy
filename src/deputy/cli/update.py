"""`deputy update`: check for a newer release and upgrade, only when asked (or opted in)."""

from __future__ import annotations

import argparse
import sys

from deputy import updates
from deputy.cli._common import Subparsers
from deputy.config import ConfigError, load_config


def run(args: argparse.Namespace) -> int:
    info = updates.check(force=True)
    if info.latest is None:
        print("could not reach GitHub to look for a newer version", file=sys.stderr)
        return 1
    if not info.available:
        print(f"deputy {info.current} is up to date")
        return 0
    print(f"deputy {info.latest} is available (you have {info.current})")
    if args.check:
        return 0
    try:
        print(updates.upgrade(info.latest))
    except updates.UpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def apply_if_opted_in() -> str | None:
    """At server start: with `auto_update = "apply"`, install a newer compatible release.

    Returns a one-line report, or None when nothing was done. Never raises.
    """
    try:
        if load_config().auto_update != "apply":
            return None
        info = updates.check()
        if not info.compatible or info.latest is None:
            return None
        return updates.upgrade(info.latest)
    except (ConfigError, updates.UpdateError) as exc:
        return f"auto-update skipped: {exc}"


def register(sub: Subparsers) -> None:
    parser = sub.add_parser("update", help="check for a newer release and upgrade")
    parser.add_argument("--check", action="store_true", help="only look, change nothing")
    parser.set_defaults(func=run)
