"""`deputy optimise`: propose a better worker profile, judged on the benchmark; you approve it."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from deputy import optimiser
from deputy.cli._common import Subparsers
from deputy.config import ConfigError, load_config
from deputy.profiles import load_profiles


def _run(args: argparse.Namespace) -> int:
    config = load_config()
    profiles = load_profiles(Path.cwd())
    profile = profiles.get(args.profile)
    if profile is None:
        raise ConfigError(f"unknown profile '{args.profile}'; known: {', '.join(profiles)}")
    report, winner = asyncio.run(
        optimiser.optimise(
            config.backend(args.backend),
            profile,
            runs=args.runs,
            policy=config.policy,
            log=lambda line: print(f"== {line}", file=sys.stderr, flush=True),
        )
    )
    folder = optimiser.save(report, profile, winner)
    print(optimiser.to_markdown(report))
    print(f"saved: {folder}")
    return 0


def optimise(args: argparse.Namespace) -> int:
    try:
        if args.list:
            print("\n".join(optimiser.listing()) or "no proposals yet")
            return 0
        if args.apply:
            target = optimiser.apply(args.apply)
            print(f"applied: {target} (undo with: deputy optimise --revert {args.apply})")
            return 0
        if args.revert:
            print(f"reverted: {optimiser.revert(args.revert)}")
            return 0
        return _run(args)
    except (ConfigError, optimiser.OptimiserError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def register(sub: Subparsers) -> None:
    parser = sub.add_parser(
        "optimise", help="propose a better worker profile (you approve it before it is used)"
    )
    parser.add_argument("--backend", help="backend name (default: the default backend)")
    parser.add_argument("--profile", default="helper", help="the profile to improve")
    parser.add_argument("--runs", type=int, default=1, help="repetitions of each measurement")
    parser.add_argument("--list", action="store_true", help="list saved proposals")
    parser.add_argument("--apply", metavar="ID", help="install a confirmed proposal")
    parser.add_argument("--revert", metavar="ID", help="undo an applied proposal")
    parser.set_defaults(func=optimise)
