# pyright: basic
import json
from pathlib import Path

from deputy import discover, spend


def _request(i: str, *, read: int = 0, write: int = 0, fresh: int = 0, out: int = 0) -> str:
    usage = {
        "input_tokens": fresh,
        "cache_creation_input_tokens": write,
        "cache_read_input_tokens": read,
        "output_tokens": out,
    }
    return json.dumps({"type": "assistant", "message": {"id": i, "usage": usage, "content": []}})


def test_each_kind_of_token_has_its_weight() -> None:
    one = spend.measure([_request("a", fresh=100, write=1_000, read=10_000, out=10)]).spend
    assert (one.conversations, one.requests) == (1, 1)
    assert one.weighted == 100 + 1_250 + 1_000 + 50
    assert one.replay == 1_000


def test_a_request_written_twice_counts_once_with_its_last_usage() -> None:
    lines = [_request("a", out=1), _request("a", out=40), "not json", json.dumps([1])]
    one = spend.measure(lines).spend
    assert (one.requests, one.weighted) == (1, 200)


def test_only_the_context_above_the_large_mark_counts_as_over_it() -> None:
    lines = [_request("a", read=150_000), _request("b", read=500_000)]
    assert spend.measure(lines).spend.over_large == 0.1 * 300_000


def test_a_compaction_records_the_context_it_found() -> None:
    lines = [
        _request("a", read=900_000, write=50_000, fresh=5),
        json.dumps({"isCompactSummary": True, "message": {"content": "summary"}}),
        _request("b", read=0, write=90_000),
    ]
    one = spend.measure(lines)
    assert one.compacted_at == [950_005]
    assert one.contexts == [950_005, 90_000]


def test_a_transcript_without_requests_is_no_conversation() -> None:
    lines = ['{"type": "user", "message": {"content": "hi"}}']
    assert spend.measure(lines).spend.conversations == 0


def _transcript(path: Path, *lines: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def _edit(i: str) -> str:
    block = {"type": "tool_use", "id": i, "name": "Edit", "input": {"file_path": "/repo/a.py"}}
    return json.dumps({"cwd": "/repo", "message": {"content": [block]}})


def test_discover_splits_the_spend_between_sessions_and_the_two_kinds_of_subagent(
    tmp_path: Path,
) -> None:
    _transcript(tmp_path / "s.jsonl", _request("a", read=400_000), _request("b", read=600_000))
    agents = tmp_path / "s" / "subagents"
    _transcript(agents / "agent-1.jsonl", _edit("e"), _request("c", read=100_000, out=1_000))
    _transcript(agents / "agent-2.jsonl", _request("d", write=8_000))
    report = discover.build_report(tmp_path, days=30).spend
    assert (report.main.requests, report.editing.requests, report.reading.requests) == (2, 1, 1)
    assert (report.main.weighted, report.editing.weighted, report.reading.weighted) == (
        100_000,
        15_000,
        10_000,
    )
    assert (report.context_median, report.context_p90, report.compacted_at) == (500_000, 600_000, 0)
    assert report.main.over_large == 60_000


def test_the_report_says_what_carrying_the_context_and_the_delegable_reads_were_worth(
    tmp_path: Path,
) -> None:
    read = {"type": "tool_use", "id": "r", "name": "Read", "input": {"file_path": "/repo/a.md"}}
    result = {"type": "tool_result", "tool_use_id": "r", "content": "x" * 8_000}
    other = {"type": "tool_use", "id": "o", "name": "Glob", "input": {"pattern": "*"}}
    _transcript(
        tmp_path / "s.jsonl",
        json.dumps({"cwd": "/repo", "message": {"content": [read]}}),
        json.dumps({"cwd": "/repo", "message": {"content": [result]}}),
        json.dumps({"cwd": "/repo", "message": {"content": [other]}}),
        _request("a", read=400_000),
    )
    report = discover.build_report(tmp_path, days=30)
    # 2,000 tokens, written once and carried by the one tool call after it
    assert report.adoption.delegable_weighted == 2_000 * (1.25 + 0.1)
    text = discover.format_report(report)
    assert "main sessions" in text
    assert "subagents" not in text.split("Where the tokens went")[1]
    assert "Reading again the context above 200,000 tokens: 50% of everything" in text
    assert "could have taken: at most 7% of everything" in text
    assert "never compacted" in text
    assert json.loads(discover.report_as_json(report))["spend"]["main"]["requests"] == 1


def test_a_compaction_ends_the_replays_of_what_was_read_before_it(tmp_path: Path) -> None:
    read = {"type": "tool_use", "id": "r", "name": "Read", "input": {"file_path": "/repo/a.md"}}
    result = {"type": "tool_result", "tool_use_id": "r", "content": "x" * 8_000}
    later = [
        json.dumps({"cwd": "/repo", "message": {"content": [
            {"type": "tool_use", "id": f"g{n}", "name": "Glob", "input": {"pattern": "*"}}
        ]}})
        for n in range(3)
    ]  # fmt: skip
    _transcript(
        tmp_path / "s.jsonl",
        json.dumps({"cwd": "/repo", "message": {"content": [read]}}),
        json.dumps({"cwd": "/repo", "message": {"content": [result]}}),
        later[0],
        json.dumps({"isCompactSummary": True, "message": {"content": "summary"}}),
        *later[1:],
    )
    report = discover.build_report(tmp_path, days=30)
    assert report.adoption.delegable_weighted == 2_000 * (1.25 + 0.1)


def test_a_report_without_usage_has_no_spend_section(tmp_path: Path) -> None:
    read = {"type": "tool_use", "id": "r", "name": "Read", "input": {"file_path": "/repo/a.md"}}
    result = {"type": "tool_result", "tool_use_id": "r", "content": "x" * 8_000}
    _transcript(
        tmp_path / "s.jsonl",
        json.dumps({"cwd": "/repo", "message": {"content": [read]}}),
        json.dumps({"cwd": "/repo", "message": {"content": [result]}}),
    )
    assert "Where the tokens went" not in discover.format_report(
        discover.build_report(tmp_path, days=30)
    )
