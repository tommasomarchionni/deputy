# pyright: basic
import asyncio
import itertools
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from deputy import scan
from deputy.agent import run_task
from deputy.backends import ChatClient
from deputy.checks import CitesLines, Contains
from deputy.config import Policy
from deputy.profiles import load_profiles
from deputy.sandbox import LARGE_FILE_CHARS, Sandbox
from helpers import message, tool_call


def big_file(root: Path, lines: int = 2500, width: int = 60) -> list[str]:
    rows = [
        f"2026-11-17 INFO worker-{i % 5} heartbeat ok {'x' * (width - 40)}"
        for i in range(1, lines + 1)
    ]
    (root / "huge.log").write_text("\n".join(rows) + "\n")
    return rows


def test_chunks_cover_the_file_exactly_and_keep_the_original_line_numbers() -> None:
    lines = [f"row {i}" for i in range(1, 101)]
    chunks = scan.split(lines, max_chars=200)
    assert len(chunks) > 3
    assert chunks[0].first == 1
    assert chunks[-1].last == 100
    for before, after in itertools.pairwise(chunks):
        assert after.first == before.last + 1  # no gap, no overlap
    for chunk in chunks:
        assert len(chunk.text) <= 200
        assert chunk.text.splitlines()[0].startswith(f"{chunk.first}: ")
        assert chunk.text.splitlines()[-1].startswith(f"{chunk.last}: ")


def test_an_empty_file_has_no_chunks_and_a_huge_line_is_cut_to_fit() -> None:
    assert scan.split([]) == []
    (chunk,) = scan.split(["y" * 100_000], max_chars=1_000)
    assert len(chunk.text) <= 1_000


@given(st.lists(st.text(alphabet="abc ", max_size=80), max_size=120), st.integers(60, 400))
def test_any_file_is_covered_in_order_by_chunks_that_fit(lines: list[str], limit: int) -> None:
    chunks = scan.split(lines, max_chars=limit)
    numbers = [int(row.split(":", 1)[0]) for chunk in chunks for row in chunk.text.splitlines()]
    assert numbers == list(range(1, len(lines) + 1))
    assert all(len(chunk.text) <= limit for chunk in chunks)


def scripted(replies: list[str]):  # type: ignore[no-untyped-def]
    return [message(reply) for reply in replies]


def test_the_findings_of_each_part_are_merged_under_the_lines_they_came_from(  # type: ignore[no-untyped-def]
    fake_client, tmp_path: Path
) -> None:
    rows = big_file(tmp_path)
    parts = scan.split(rows)
    assert len(parts) >= 4
    replies = [scan.NOTHING] * len(parts)
    replies[2] = f"{parts[2].first + 3}: worker-13 appears here"
    client = fake_client(scripted(replies))
    sent: list[str] = []
    original = client.chat

    async def spy(messages, tools=None):  # type: ignore[no-untyped-def]
        sent.append(messages[1]["content"])
        return await original(messages, tools)

    client.chat = spy  # type: ignore[method-assign]
    box = Sandbox(tmp_path)
    out = asyncio.run(scan.scan_file(client, box, "huge.log", "which worker id is unusual?"))
    assert f"[lines {parts[2].first}-{parts[2].last}]" in out
    assert f"{parts[2].first + 3}: worker-13 appears here" in out
    assert out.count("[lines ") == 1  # parts with nothing relevant are left out
    assert f"in {len(parts)} part(s)" in out
    assert all("Question: which worker id is unusual?" in prompt for prompt in sent)
    assert f"{parts[3].first}: 2026" in sent[3]  # a later part still carries its real numbers
    assert box.chars_read >= sum(
        len(r) for r in rows
    )  # counted as read on the orchestrator's behalf


def test_a_file_with_nothing_relevant_says_so(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    rows = big_file(tmp_path, lines=800)
    client = fake_client(scripted([scan.NOTHING] * len(scan.split(rows))))
    out = asyncio.run(scan.scan_file(client, Sandbox(tmp_path), "huge.log", "anything odd?"))
    assert "Nothing relevant" in out


def test_a_file_with_too_many_parts_is_refused_with_a_way_forward(
    fake_client, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    big_file(tmp_path, lines=9000)
    out = asyncio.run(scan.scan_file(fake_client([]), Sandbox(tmp_path), "huge.log", "q"))
    assert out.startswith("ERROR")
    assert "grep" in out


def test_a_model_failure_in_the_middle_is_reported_not_raised(fake_client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    big_file(tmp_path, lines=1500)
    client = fake_client([message(scan.NOTHING), 500])
    out = asyncio.run(scan.scan_file(client, Sandbox(tmp_path), "huge.log", "q"))
    assert out.startswith("ERROR: the scan stopped at lines")


def test_a_missing_or_forbidden_file_is_an_error_for_the_worker(
    fake_client, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / ".env").write_text("TOKEN=1")
    for path in ("nope.log", "../x", ".env"):
        out = asyncio.run(scan.scan_file(fake_client([]), Sandbox(tmp_path), path, "q"))
        assert out.startswith("ERROR")


def test_a_read_of_a_large_file_points_the_worker_at_scan_file(tmp_path: Path) -> None:
    (tmp_path / "huge.log").write_text(
        "x" * 100 + "\n" * 0 + ("y" * 99 + "\n") * (LARGE_FILE_CHARS // 100 + 10)
    )
    assert "scan_file" in Sandbox(tmp_path).read_file("huge.log")
    (tmp_path / "small.log").write_text("short\n")
    assert "scan_file" not in Sandbox(tmp_path).read_file("small.log")


def test_a_worker_scans_a_file_too_large_to_read_and_cites_the_real_line(  # type: ignore[no-untyped-def]
    fake_client, tmp_path: Path
) -> None:
    rows = big_file(tmp_path)
    parts = scan.split(rows)
    target = parts[1].first + 7
    chunk_replies = [scan.NOTHING] * len(parts)
    chunk_replies[1] = f"{target}: the unusual id is worker-13"
    script = [
        message(
            tool_calls=[tool_call("scan_file", {"path": "huge.log", "question": "unusual id?"})]
        ),
        *scripted(chunk_replies),
        message(f"The unusual id is worker-13, on line {target}."),
    ]
    client: ChatClient = fake_client(script)
    result = asyncio.run(
        run_task(
            client=client, profile=load_profiles()["helper"], task="find the unusual worker id",
            sandbox=Sandbox(tmp_path), policy=Policy(),
            checks=[Contains(values=["worker-13"]), CitesLines(min=target, max=target)],
        )
    )  # fmt: skip
    assert result.status == "ok", result.checks
    assert result.metrics.tool_calls == 1
    assert (
        result.orchestrator_tokens_saved_est > 20_000
    )  # a ~150 KB file the orchestrator never read


def test_the_bundled_profile_tells_the_worker_about_scan_file() -> None:
    assert "scan_file" in load_profiles()["helper"].body


def test_a_scan_that_runs_out_of_time_returns_what_it_found_and_says_where_it_stopped(  # type: ignore[no-untyped-def]
    fake_client, tmp_path: Path, monkeypatch
) -> None:
    rows = big_file(tmp_path, lines=1500)
    parts = scan.split(rows)
    client = fake_client(scripted([f"{parts[0].first}: first part finding"] + [scan.NOTHING] * 10))
    ticks = iter([0.0, 0.0, 10_000.0, 10_000.0, 10_000.0, 10_000.0, 10_000.0])
    monkeypatch.setattr(scan, "_now", lambda: next(ticks))
    out = asyncio.run(scan.scan_file(client, Sandbox(tmp_path), "huge.log", "q"))
    assert "first part finding" in out
    assert f"stopped at line {parts[1].first}" in out
