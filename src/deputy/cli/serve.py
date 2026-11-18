"""`deputy mcp`: run the MCP server on stdio."""

from __future__ import annotations

import argparse
import sys

from deputy import install
from deputy.cli import update
from deputy.cli._common import Subparsers


def run(_: argparse.Namespace) -> int:
    from deputy.server import build_server  # noqa: PLC0415  (slow import, needed only here)

    report = update.apply_if_opted_in()
    if report:
        print(f"deputy: {report}", file=sys.stderr)  # stdout belongs to the MCP protocol
    if install.refresh_user_skill():
        print("deputy: refreshed the user-level skill to this version's guide", file=sys.stderr)
    if install.refresh_codex_guide():
        print("deputy: refreshed the guide in Codex's AGENTS.md", file=sys.stderr)
    build_server().run("stdio")
    return 0


def register(sub: Subparsers) -> None:
    sub.add_parser("mcp", help="run the MCP server on stdio").set_defaults(func=run)
