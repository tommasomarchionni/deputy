"""`deputy hook`: the Claude Code hint that suggests delegating a large read."""

from __future__ import annotations

import argparse
import sys

from deputy import hook
from deputy.cli._common import Subparsers


def run(args: argparse.Namespace) -> int:
    if args.action == "read-hint":
        out = hook.run(sys.stdin.read())
        if out:
            print(out)
        return 0  # a hook must never block the tool call
    try:
        if args.action == "on":
            print(hook.enable())
        elif args.action == "off":
            print(hook.disable())
        else:
            state = "on" if hook.is_enabled() else "off"
            print(f"read hint {state} ({hook.settings_path()})")
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def register(sub: Subparsers) -> None:
    parser = sub.add_parser(
        "hook", help="the Claude Code hint that suggests delegating a large read (on by default)"
    )
    parser.add_argument("action", choices=["on", "off", "status", "read-hint"])
    parser.set_defaults(func=run)
