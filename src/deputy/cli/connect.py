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
        print(
            installer.apply(
                args.agent, Path.cwd(), read_hint=not args.no_hook, skill=not args.no_skill
            )
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def instructions(args: argparse.Namespace) -> int:
    if args.write:
        print(installer.write_instructions(Path(args.write)))
    elif args.target and args.apply:
        print(installer.apply_instructions(args.target, Path.cwd()))
    elif args.target:
        print(installer.render_instructions(args.target), end="")
    else:
        print(installer.instructions_text())
    return 0


def register(sub: Subparsers) -> None:
    install_parser = sub.add_parser("install", help="connect deputy to an agent")
    install_parser.add_argument("agent", choices=installer.AGENTS)
    install_parser.add_argument("--apply", action="store_true", help="do it instead of printing")
    install_parser.add_argument(
        "--no-hook",
        action="store_true",
        help="claude-code: do not add the hint that suggests delegating large reads",
    )
    install_parser.add_argument(
        "--no-skill",
        action="store_true",
        help="claude-code: do not install the delegation guide as a user-level skill",
    )
    install_parser.set_defaults(func=install)

    guide = sub.add_parser("instructions", help="print or write the delegation guide for agents")
    guide.add_argument(
        "--for",
        dest="target",
        choices=sorted(installer.INSTRUCTION_TARGETS),
        help="the format of this agent: "
        + "; ".join(f"{n}: {t.note}" for n, t in installer.INSTRUCTION_TARGETS.items()),
    )
    guide.add_argument(
        "--apply", action="store_true", help="with --for: write it where the agent reads it"
    )
    guide.add_argument("--write", metavar="FILE", help="insert the guide as a marked block in FILE")
    guide.set_defaults(func=instructions)
