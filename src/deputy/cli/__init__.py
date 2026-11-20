"""The `deputy` command line."""

from __future__ import annotations

import argparse

from deputy.cli import (
    connect,
    discover,
    hook,
    serve,
    setup,
    stats,
    tuning,
    update,
    wait,
)
from deputy.version import current_version

_COMMAND_MODULES = (
    setup,
    connect,
    tuning,
    stats,
    discover,
    hook,
    wait,
    update,
    serve,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deputy", description=__doc__)
    parser.add_argument("--version", action="version", version=f"deputy {current_version()}")
    sub = parser.add_subparsers(dest="command", required=True)
    for module in _COMMAND_MODULES:
        module.register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
