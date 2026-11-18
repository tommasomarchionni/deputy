"""`deputy stats`: what deputy did for you, and what it saved."""

from __future__ import annotations

import argparse

from deputy import stats
from deputy.cli._common import Subparsers


def run(args: argparse.Namespace) -> int:
    events, unreadable = stats.read_trace()
    report = stats.build_report(events, days=args.days, unreadable=unreadable)
    print(stats.report_as_json(report) if args.json else stats.format_report(report))
    return 0


def register(sub: Subparsers) -> None:
    parser = sub.add_parser("stats", help="usage, acceptance and tokens saved, from the trace")
    parser.add_argument("--days", type=float, default=30, help="how far back to look (default 30)")
    parser.add_argument("--json", action="store_true", help="print JSON, not a table")
    parser.set_defaults(func=run)
