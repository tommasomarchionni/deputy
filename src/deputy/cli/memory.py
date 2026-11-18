"""`deputy memory`: what deputy remembers about a repository. Workers cannot change it."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from deputy import project_memory as pm
from deputy.cli._common import Subparsers


def _root(args: argparse.Namespace) -> Path:
    return pm.repo_root(Path(args.repo))


def _edit(args: argparse.Namespace, root: Path, memory: pm.ProjectMemory) -> pm.ProjectMemory:
    if args.action == "pointer":
        return pm.add_pointer(memory, root, args.key, args.text or "")
    if args.action == "term":
        return pm.add_term(memory, args.key, args.text or "")
    return pm.remove(memory, args.key)


def _scan(args: argparse.Namespace, root: Path, memory: pm.ProjectMemory) -> int:
    found = pm.candidates(root)
    known = {p.path for p in memory.pointers}
    new = [p for p in found if p.path not in known]
    for pointer in new:
        print(f"{pointer.path}: {pointer.why}")
    if not new:
        print("no new rule files found")
    elif args.apply:
        for pointer in new:
            memory = pm.add_pointer(memory, root, pointer.path, pointer.why)
        pm.save(root, memory)
        print(f"\nsaved {len(new)} pointer(s)")
    else:
        print("\nreport only: `deputy memory scan --apply` remembers these")
    return 0


def run(args: argparse.Namespace) -> int:
    root = _root(args)
    memory = pm.load(root)
    try:
        if args.action == "scan":
            return _scan(args, root, memory)
        if args.action in ("pointer", "term", "remove"):
            if not args.key:
                print("error: this action needs a path or a term", file=sys.stderr)
                return 1
            pm.save(root, _edit(args, root, memory))
            print("saved")
            return 0
    except pm.ProjectMemoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(pm.to_markdown(memory), end="")  # show and export are the same reviewable text
    return 0


def register(sub: Subparsers) -> None:
    parser = sub.add_parser("memory", help="what deputy remembers about a repository")
    parser.add_argument(
        "action",
        choices=["show", "export", "scan", "pointer", "term", "remove"],
        nargs="?",
        default="show",
    )
    parser.add_argument("key", nargs="?", help="pointer: a file; term: the word; remove: either")
    parser.add_argument("text", nargs="?", help="pointer: why it matters; term: what it means")
    parser.add_argument(
        "--repo", default=".", help="a folder inside the repository (default: here)"
    )
    parser.add_argument("--apply", action="store_true", help="scan: remember what it finds")
    parser.set_defaults(func=run)
