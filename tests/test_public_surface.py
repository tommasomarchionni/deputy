# pyright: basic
"""Snapshot (regression) tests of what users and agents see: the tool schemas, the command line
help and the benchmark report. A change here is a change of the public surface: review the diff
and update the snapshots on purpose (`uv run pytest --snapshot-update`)."""

import argparse
import asyncio
import json

import pytest
from syrupy.assertion import SnapshotAssertion

from deputy.bench import Summary
from deputy.cli import build_parser
from deputy.cli.tuning import format_table
from deputy.server import INSTRUCTIONS, build_server


def test_the_tool_schemas_are_stable(snapshot: SnapshotAssertion) -> None:
    tools = asyncio.run(build_server().list_tools())
    surface = {
        t.name: {"description": t.description, "input": t.model_dump(mode="json")["input_schema"]}
        for t in tools
    }
    assert json.dumps(surface, indent=2, sort_keys=True) == snapshot


def test_the_server_instructions_are_stable(snapshot: SnapshotAssertion) -> None:
    assert snapshot == INSTRUCTIONS


def _subcommands() -> list[str]:
    parser = build_parser()
    (action,) = (a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return sorted(action.choices)


def test_the_list_of_commands_is_stable(snapshot: SnapshotAssertion) -> None:
    assert _subcommands() == snapshot


@pytest.mark.parametrize("command", _subcommands())
def test_each_command_help_is_stable(command: str, snapshot: SnapshotAssertion) -> None:
    parser = build_parser()
    (action,) = (a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    assert action.choices[command].format_help() == snapshot


def test_the_benchmark_table_is_stable(snapshot: SnapshotAssertion) -> None:
    rows = [
        Summary(variant="baseline", runs=5, pass_rate=0.8, citation_rate=1.0, mean_turns=2.4,
                mean_tokens_out=912.0, mean_elapsed_s=61.5, stdev_elapsed_s=7.2, tokens_per_s=14.8,
                mean_saved_tokens_est=1530.0),
        Summary(variant="thinking-off", runs=5, pass_rate=0.8, citation_rate=1.0, mean_turns=2.2,
                mean_tokens_out=311.0, mean_elapsed_s=24.0, stdev_elapsed_s=3.1, tokens_per_s=13.0,
                mean_saved_tokens_est=1530.0),
    ]  # fmt: skip
    assert format_table(rows) == snapshot
