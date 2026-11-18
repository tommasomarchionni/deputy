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


def test_delegations_to_deputy_are_counted_next_to_the_large_reads(tmp_path: Path) -> None:
    _session(
        tmp_path,
        "s",
        _use("a", "mcp__deputy__delegate", task="summarise docs/big.md"),
        _result("a", '{"status":"ok"}'),
        _use("b", "mcp__deputy__delegate", task="list the ADRs"),
        _result("b", '{"status":"ok"}'),
        _use("c", "Read", file_path="big.md"),
        _result("c", "x" * 40_000),
        _use("d", "mcp__other__delegate_task"),
        _result("d", "ok"),
    )
    report = discover.build_report(tmp_path, days=30)
    assert report.delegations == 2
    text = discover.format_report(report)
    assert "2 delegations; 1 large file reads a delegation could have taken" in text
    assert json.loads(discover.report_as_json(report))["delegations"] == 2


def _at(cwd: str, *blocks: dict) -> list[str]:
    return [json.dumps({"cwd": cwd, "message": {"content": [b]}}) for b in blocks]


def _write(folder: Path, lines: list[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "s.jsonl").write_text("\n".join(lines) + "\n")


BIG = "x" * 8_000  # 2,000 tokens


def test_reads_through_the_shell_count_as_file_reads(tmp_path: Path) -> None:
    lines = _at(
        "/repo",
        _use("a", "Read", file_path="/repo/a.md"),
        _result("a", BIG),
        _use("b", "Bash", command="sed -n 1,400p b.md"),
        _result("b", BIG),
        _use("c", "Bash", command="cd sub && cat c.md"),
        _result("c", BIG),
        _use("d", "Bash", command="git log"),
        _result("d", BIG),
    )
    _write(tmp_path, lines)
    seen = discover.build_report(tmp_path, days=30).adoption
    assert (seen.file_reads, seen.delegable_reads, seen.delegable_tokens) == (3, 3, 6_000)


def test_a_read_followed_by_an_edit_of_that_file_is_not_delegable(tmp_path: Path) -> None:
    lines = _at(
        "/repo",
        _use("a", "Bash", command="cat a.md"),
        _result("a", BIG),
        _use("b", "Edit", file_path="/repo/a.md"),
        _result("b", "ok"),
        _use("c", "Read", file_path="/repo/other.md"),
        _result("c", BIG),
        _use("d", "Edit", file_path="/repo/unrelated.md"),
        _result("d", "ok"),
    )
    _write(tmp_path, lines)
    seen = discover.build_report(tmp_path, days=30).adoption
    assert (seen.file_reads, seen.delegable_reads) == (2, 1)


def test_an_edit_long_after_the_read_does_not_excuse_it(tmp_path: Path) -> None:
    filler: list[dict] = []
    for i in range(discover.EDIT_WINDOW):
        filler += [_use(f"f{i}", "Bash", command="true"), _result(f"f{i}", "ok")]
    lines = _at(
        "/repo",
        _use("a", "Read", file_path="/repo/a.md"),
        _result("a", BIG),
        *filler,
        _use("b", "Edit", file_path="/repo/a.md"),
        _result("b", "ok"),
    )
    _write(tmp_path, lines)
    assert discover.build_report(tmp_path, days=30).adoption.delegable_reads == 1


def test_reading_an_unchanged_file_again_is_counted(tmp_path: Path) -> None:
    lines = _at(
        "/repo",
        _use("a", "Bash", command="sed -n 1,300p a.md"),
        _result("a", BIG),
        _use("b", "Bash", command="sed -n 1,300p a.md"),
        _result("b", BIG),
        _use("c", "Bash", command="sed -n 301,600p a.md"),
        _result("c", BIG),
    )
    _write(tmp_path, lines)
    seen = discover.build_report(tmp_path, days=30).adoption
    assert (seen.again_tokens, seen.same_part_tokens) == (4_000, 2_000)


@pytest.mark.parametrize("between", ["edit", "compaction"])
def test_a_read_after_an_edit_or_a_compaction_is_not_a_repeat(tmp_path: Path, between: str) -> None:
    first = _at("/repo", _use("a", "Read", file_path="/repo/a.md"), _result("a", BIG))
    second = _at("/repo", _use("c", "Read", file_path="/repo/a.md"), _result("c", BIG))
    middle = (
        _at("/repo", _use("b", "Edit", file_path="/repo/a.md"), _result("b", "ok"))
        if between == "edit"
        else [json.dumps({"isCompactSummary": True, "message": {"content": "summary"}})]
    )
    _write(tmp_path, [*first, *middle, *second])
    assert discover.build_report(tmp_path, days=30).adoption.again_tokens == 0


def test_the_adoption_rate_is_shown_and_exported(tmp_path: Path) -> None:
    lines = _at(
        "/repo",
        _use("a", "mcp__deputy__delegate", task="t"),
        _result("a", "ok"),
        _use("b", "Read", file_path="/repo/a.md"),
        _result("b", BIG),
    )
    _write(tmp_path, lines)
    report = discover.build_report(tmp_path, days=30)
    assert report.adoption_rate == 0.5
    assert "Adoption: 50.0% (1 delegations" in discover.format_report(report)
    assert json.loads(discover.report_as_json(report))["adoption_rate"] == 0.5
