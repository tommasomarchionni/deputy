"""Connect deputy to an agent, and teach the agent when to delegate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from deputy import install as installer
from deputy.cli._common import Subparsers


def install(args: argparse.Namespace) -> int:
    print(installer.snippet(args.agent))
    if not args.apply:
        print("\n(printed only; add --apply to do it for you)")
        return 0
    try:
        print(installer.apply(args.agent, Path.cwd()))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def instructions(args: argparse.Namespace) -> int:
    if args.write:
        print(installer.write_instructions(Path(args.write)))
    else:
        print(installer.instructions_text())
    return 0


def register(sub: Subparsers) -> None:
    install_parser = sub.add_parser("install", help="connect deputy to an agent")
    install_parser.add_argument("agent", choices=installer.AGENTS)
    install_parser.add_argument("--apply", action="store_true", help="do it instead of printing")
    install_parser.set_defaults(func=install)

    guide = sub.add_parser("instructions", help="print or write the delegation guide for agents")
    guide.add_argument("--write", metavar="FILE", help="insert it in FILE (CLAUDE.md, ...)")
    guide.set_defaults(func=instructions)
