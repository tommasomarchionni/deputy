"""`deputy discover`: where the orchestrator's reading goes, and what delegation could take."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from deputy import discover
from deputy.cli._common import Subparsers


def run(args: argparse.Namespace) -> int:
    folder = (
        Path(args.transcripts) if args.transcripts else discover.transcripts_dir(Path(args.project))
    )
    if not folder.is_dir():
        print(f"error: no Claude Code sessions found in {folder}", file=sys.stderr)
        return 1
    report = discover.build_report(folder, days=args.days)
    print(discover.report_as_json(report) if args.json else discover.format_report(report))
    return 0


def register(sub: Subparsers) -> None:
    parser = sub.add_parser(
        "discover", help="what your orchestrator read by itself, from local Claude Code sessions"
    )
    parser.add_argument("--project", default=".", help="project folder (default: here)")
    parser.add_argument("--transcripts", help="a folder of session transcripts (*.jsonl)")
    parser.add_argument("--days", type=float, default=30, help="how far back to look (default 30)")
    parser.add_argument("--json", action="store_true", help="print JSON, not a table")
    parser.set_defaults(func=run)
