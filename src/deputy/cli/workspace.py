"""`deputy workspace`: disposable copies of a repository for workers to edit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from deputy import workspace
from deputy.cli._common import Subparsers


def run(args: argparse.Namespace) -> int:
    try:
        if args.action == "create":
            print(workspace.create(args.name, Path(args.repo), args.paths))
        elif args.action == "diff":
            print(workspace.diff(args.name) or "no changes")
        elif args.action == "path":
            print(workspace.work_dir(args.name))
        else:
            workspace.remove(args.name)
            print(f"removed {args.name}")
    except workspace.WorkspaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def register(sub: Subparsers) -> None:
    parser = sub.add_parser("workspace", help="disposable copies of a repository for workers")
    parser.add_argument("action", choices=["create", "diff", "path", "remove"])
    parser.add_argument("name")
    parser.add_argument("paths", nargs="*", help="files or folders to copy (create only)")
    parser.add_argument("--repo", default=".", help="repository the paths are relative to")
    parser.set_defaults(func=run)
