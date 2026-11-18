# pyright: basic
import json
import os
import time
from pathlib import Path

import pytest

from deputy import cli, discover


def _use(i: str, name: str, **arguments: object) -> dict:
    return {"type": "tool_use", "id": i, "name": name, "input": arguments}


def _result(i: str, content: object) -> dict:
    return {"type": "tool_result", "tool_use_id": i, "content": content}


def _session(folder: Path, name: str, *blocks: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.jsonl"
    lines = [json.dumps({"message": {"content": [b]}}) for b in blocks]
    path.write_text("\n".join([*lines, "not json", json.dumps({"message": "x"})]) + "\n")
    return path


def test_large_reads_and_command_output_are_counted_apart(tmp_path: Path) -> None:
    _session(
        tmp_path,
        "s1",
        _use("a", "Read", file_path="big.md"),
        _result("a", "x" * 40_000),
        _use("b", "Bash", command="cd /repo && git diff HEAD~3"),
        _result("b", "y" * 8_000),
        _use("c", "Bash", command="sed -n 1,20p small.py"),
        _result("c", "z" * 400),
        _use("d", "Edit", file_path="a.py"),
        _result("d", "ok"),
    )
    report = discover.build_report(tmp_path, days=30)
    assert report.sessions == 1
    assert report.total.results == 4
    reads = report.by_category["reads"]
    assert (reads.results, reads.large_results, reads.large_tokens) == (2, 1, 10_000)
    assert report.by_category["command output"].large_tokens == 2_000
    assert report.by_source["Bash: git"].results == 1
    assert report.by_category["other"].results == 1


def test_images_are_ignored_and_only_text_counts(tmp_path: Path) -> None:
    content = [
        {"type": "image", "source": {"data": "A" * 500_000}},
        {"type": "text", "text": "abcd" * 10},
    ]
    _session(tmp_path, "s", _use("a", "Read", file_path="shot.png"), _result("a", content))
    assert discover.build_report(tmp_path, days=30).total.tokens == 10


def test_old_sessions_are_left_out(tmp_path: Path) -> None:
    path = _session(tmp_path, "old", _use("a", "Read"), _result("a", "x" * 10))
    old = time.time() - 40 * 86_400
    os.utime(path, (old, old))
    assert discover.build_report(tmp_path, days=30).sessions == 0


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("Grep", {}, "reads"),
        ("Bash", {"command": "FOO=1 cat x"}, "reads"),
        ("Bash", {"command": "/usr/bin/grep -rn x ."}, "reads"),
        ("Bash", {"command": "gh run list"}, "command output"),
        ("Bash", {"command": "python3 -c 1"}, "other"),
        ("Write", {}, "other"),
    ],
)
def test_each_call_gets_a_category(name: str, arguments: dict, expected: str) -> None:
    assert discover.category_of(discover.source_of(name, arguments)) == expected


def test_the_report_states_both_upper_bounds(tmp_path: Path) -> None:
    _session(tmp_path, "s", _use("a", "Read"), _result("a", "x" * 40_000))
    text = discover.format_report(discover.build_report(tmp_path, days=30))
    assert "A delegation could take up to ~10,000 tokens of large reads (100% of all)" in text
    assert "not cost" in text


def test_the_command_reads_a_folder_and_fails_clearly_without_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _session(tmp_path / "t", "s", _use("a", "Read"), _result("a", "x" * 100))
    assert cli.main(["discover", "--transcripts", str(tmp_path / "t"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["total"]["results"] == 1
    assert cli.main(["discover", "--transcripts", str(tmp_path / "missing")]) == 1


def test_the_transcripts_folder_follows_the_claude_code_naming(tmp_path: Path) -> None:
    folder = discover.transcripts_dir(tmp_path / "my.project")
    assert folder.parent.name == "projects"
    assert folder.name.endswith("-my-project")
