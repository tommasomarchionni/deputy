"""`deputy wait`: block until a local agent's run ends, then print its result.

Run it in a background shell: the orchestrator is told once, when the run ends, and spends no
turn asking whether it is done.
"""

from __future__ import annotations

import argparse
import json
import sys

from deputy import localagent
from deputy.cli._common import Subparsers

TIMED_OUT = 124  # the exit code of `timeout`


def run(args: argparse.Namespace) -> int:
    text = localagent.wait_for(args.run_id, args.timeout)
    if text is None:
        print(f"error: run {args.run_id} has not ended after {args.timeout:g} s", file=sys.stderr)
        return TIMED_OUT
    print(text)
    return 0 if json.loads(text).get("status") == "ok" else 1


def register(sub: Subparsers) -> None:
    parser = sub.add_parser("wait", help="wait for a local agent's run and print its result")
    parser.add_argument("run_id", help="the run_id that `delegate` returned")
    parser.add_argument(
        "--timeout",
        type=float,
        default=localagent.BUDGET_S + 600,
        help="seconds to wait (default: longer than a run can take)",
    )
    parser.set_defaults(func=run)
