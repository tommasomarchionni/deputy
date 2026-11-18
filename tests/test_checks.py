# pyright: basic
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from deputy.checks import (
    CheckContext,
    CheckSpec,
    CitesLines,
    CitesText,
    Command,
    Contains,
    Excludes,
    FileContains,
    FileWritten,
    JsonSchema,
    MaxChars,
    appears,
    cited_numbers,
    quoted_citations,
    run_checks,
    to_check,
)
from deputy.config import Policy
from deputy.sandbox import Sandbox


def run(checks: list, answer: str, box: Sandbox, policy: Policy | None = None):  # type: ignore[type-arg]
    return asyncio.run(run_checks(checks, answer, box, policy or Policy()))


def test_contains_is_case_insensitive_and_lists_what_is_missing(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    ok = run([Contains(values=["Cosign", "digest"])], "uses COSIGN and a digest", box)
    bad = run([Contains(values=["cosign", "agent_min"])], "only cosign here", box)
    assert ok[0].passed
    assert not bad[0].passed
    assert "agent_min" in bad[0].detail


def test_cites_lines_accepts_ranges_in_two_languages(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    check = CitesLines(min=10, max=20)
    assert run([check], "rule (lines 10-12), other (righe 15, 20)", box)[0].passed
    assert run([check], "see l. 14", box)[0].passed


def test_cites_lines_rejects_out_of_range_and_missing_citations(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    check = CitesLines(min=10, max=20)
    out = run([check], "see lines 9 and 21-22", box)[0]
    assert not out.passed
    assert "9" in out.detail
    assert not run([check], "no numbers here", box)[0].passed


def test_max_chars_protects_the_orchestrator(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    assert run([MaxChars(limit=10)], "short", box)[0].passed
    assert not run([MaxChars(limit=3)], "too long", box)[0].passed


def test_file_written_needs_the_worker_to_have_written_it(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    (tmp_path / "pre.txt").write_text("existing")
    assert not run([FileWritten(path="pre.txt")], "", box)[0].passed
    box.write_file("new.txt", "x")
    assert run([FileWritten(path="new.txt")], "", box)[0].passed


def test_command_runs_only_when_the_policy_allows_it(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    argv = [sys.executable, "-c", "print('hi')"]
    denied = run([Command(argv=argv)], "", box)[0]
    allowed = run([Command(argv=argv)], "", box, Policy(allowed_commands=[[sys.executable, "-c"]]))[
        0
    ]
    assert not denied.passed
    assert "not allowed" in denied.detail
    assert allowed.passed


def test_command_failure_and_timeout_are_reported(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    policy = Policy(allowed_commands=[[sys.executable, "-c"]], command_timeout_s=0.5)
    failing = run([Command(argv=[sys.executable, "-c", "raise SystemExit(3)"])], "", box, policy)[0]
    slow = run(
        [Command(argv=[sys.executable, "-c", "import time; time.sleep(5)"])], "", box, policy
    )[0]
    assert not failing.passed
    assert slow.detail == "timed out"


def test_excludes_fails_when_forbidden_text_appears(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    assert run([Excludes(values=["SECRET"])], "all fine", box)[0].passed
    leaked = run([Excludes(values=["secret"])], "the token is SECRET-1", box)[0]
    assert not leaked.passed
    assert "SECRET-1" not in leaked.detail  # the report must not repeat the leak


def test_file_contains_reads_the_file_inside_the_sandbox(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    (tmp_path / "out.txt").write_text("hello deputy")
    assert run([FileContains(path="out.txt", values=["hello"])], "", box)[0].passed
    assert not run([FileContains(path="out.txt", values=["bye"])], "", box)[0].passed
    assert not run([FileContains(path="../x", values=["a"])], "", box)[0].passed
    assert not run([FileContains(path="missing.txt", values=["a"])], "", box)[0].passed


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("`long.log:540` has it", [540]),
        ("see src/deputy/a.py:12-14 and b.md:3", [12, 14, 3]),
        ("at 12:09:00 the worker failed", []),
        ("open http://localhost:8080/x or https://example.com:443/y", []),
        ("lines 3, 5 and file.txt:7", [3, 5, 7]),
    ],
)
def test_citation_forms_are_recognised_without_false_positives(
    answer: str, expected: list[int]
) -> None:
    assert sorted(cited_numbers(answer)) == sorted(expected)


FILE_TEXT = (
    "alpha one\nRetention of audit logs is 30 days\nthird   line  here\nlast line of the file\n"
)


@pytest.fixture
def cited(tmp_path: Path) -> Sandbox:
    (tmp_path / "doc.txt").write_text(FILE_TEXT)
    return Sandbox(tmp_path)


def cites_text(answer: str, box: Sandbox, path: str = "doc.txt"):  # type: ignore[no-untyped-def]
    return run([CitesText(path=path)], answer, box)[0]


def test_a_quote_that_is_on_the_cited_line_passes_in_every_citation_style(cited: Sandbox) -> None:
    for answer in (
        'line 2: "retention of audit logs is 30 days"',
        '2: "retention of audit logs"',
        "doc.txt:2 `audit logs is 30 days`",
        '"Retention of audit logs is 30 days" (line 2)',
        'righe 1-2: "alpha one" and "audit logs is 30 days"',
    ):
        assert cites_text(answer, cited).passed, answer


def test_a_quote_that_is_not_on_the_cited_line_fails_and_says_where(cited: Sandbox) -> None:
    result = cites_text('line 3: "retention of audit logs is 30 days"', cited)
    assert not result.passed
    assert "line 3 does not contain" in result.detail
    assert "retention of audit logs" in result.detail


def test_one_wrong_quote_among_right_ones_fails_the_check(cited: Sandbox) -> None:
    answer = 'line 1: "alpha one" and line 4: "retention of audit logs is 30 days"'
    result = cites_text(answer, cited)
    assert not result.passed
    assert "line 4" in result.detail
    assert "line 1" not in result.detail


def test_quotes_are_compared_without_regard_to_case_or_spacing(cited: Sandbox) -> None:
    assert cites_text('line 3: "THIRD line here"', cited).passed


def test_a_quote_beyond_the_end_of_the_file_fails(cited: Sandbox) -> None:
    assert not cites_text('line 99: "alpha one is here"', cited).passed


def test_an_answer_without_a_quoted_citation_fails_with_the_expected_form(cited: Sandbox) -> None:
    for answer in ("see line 2", 'it says "retention of audit logs" somewhere', 'line 2: "short"'):
        result = cites_text(answer, cited)
        assert not result.passed
        assert "no quoted citation" in result.detail


def test_an_unreadable_or_forbidden_path_fails_without_raising(cited: Sandbox) -> None:
    assert not cites_text('line 1: "alpha one is here"', cited, path="missing.txt").passed
    assert not cites_text('line 1: "alpha one is here"', cited, path="../x").passed


def test_apostrophes_in_prose_are_not_taken_for_quotes() -> None:
    assert quoted_citations("line 2: it's what they don't say, isn't it") == []


@given(
    st.lists(
        st.text(alphabet="abcdefghij klmnop", min_size=12, max_size=40), min_size=3, max_size=12
    ),
    st.data(),
)
def test_a_quote_copied_from_a_line_always_verifies_on_that_line_and_not_elsewhere(
    lines, data
) -> None:  # type: ignore[no-untyped-def]
    lines = [" ".join(line.split()) for line in lines]
    index = data.draw(st.integers(0, len(lines) - 1))
    quote = lines[index]
    if len(quote) < 8 or sum(quote in other for other in lines) != 1:
        return  # not an unambiguous quote: nothing to assert
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "f.txt").write_text("\n".join(lines))
        box = Sandbox(root)
        assert cites_text(f'line {index + 1}: "{quote}"', box, "f.txt").passed
        other = (index + 1) % len(lines) + 1
        assert not cites_text(f'line {other}: "{quote}"', box, "f.txt").passed


ITEMS = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "name"],
                "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
            },
        }
    },
}


def json_schema(answer: str, box: Sandbox, schema: dict | None = None, path: str | None = None):  # type: ignore[no-untyped-def]
    return run([JsonSchema(json_schema=schema or ITEMS, path=path)], answer, box)[0]


def test_json_that_matches_the_schema_passes_with_or_without_a_code_fence(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    document = '{"items": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]}'
    assert json_schema(document, box).passed
    assert json_schema(f"Here it is:\n```json\n{document}\n```\nDone.", box).passed


def test_a_mismatch_is_reported_with_the_path_inside_the_document(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    result = json_schema('{"items": [{"id": 1, "name": "a"}, {"id": "x", "name": "b"}]}', box)
    assert not result.passed
    assert "$.items[1].id: 'x' is not of type 'integer'" in result.detail
    missing = json_schema('{"items": [{"id": 1}]}', box)
    assert "$.items[0]: 'name' is a required property" in missing.detail
    assert "'items' is a required property" in json_schema("{}", box).detail


def test_text_that_is_not_json_says_where_it_stopped_making_sense(tmp_path: Path) -> None:
    result = json_schema("not json at all", Sandbox(tmp_path))
    assert not result.passed
    assert "not valid JSON" in result.detail
    assert "line 1 column" in result.detail


def test_many_errors_are_cut_to_the_first_few_and_counted(tmp_path: Path) -> None:
    bad = ", ".join('{"id": "x", "name": 1}' for _ in range(5))
    result = json_schema(f'{{"items": [{bad}]}}', Sandbox(tmp_path))
    assert "and 5 more" in result.detail
    assert result.detail.count("is not of type") == 5


def test_the_schema_can_validate_a_file_the_worker_wrote(tmp_path: Path) -> None:
    box = Sandbox(tmp_path)
    (tmp_path / "out.json").write_text('{"items": []}')
    assert json_schema("ignored answer", box, path="out.json").passed
    (tmp_path / "out.json").write_text('{"items": [{"id": 1}]}')
    assert not json_schema("", box, path="out.json").passed
    assert "cannot read" in json_schema("", box, path="missing.json").detail
    assert "cannot read" in json_schema("", box, path="../x.json").detail


def test_a_schema_that_is_not_a_schema_is_refused_before_any_work_starts() -> None:
    with pytest.raises(ValueError, match="not a valid JSON schema"):
        JsonSchema(json_schema={"type": "nonsense"})


def test_a_reference_to_the_network_fails_cleanly_and_never_touches_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """jsonschema downloads a `$ref` to a URL by itself unless it is given an empty registry."""
    lookups: list[object] = []
    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kw: lookups.append(args))
    schema = {"$ref": "http://example.invalid/schema.json"}
    result = json_schema('{"a": 1}', Sandbox(tmp_path), schema)
    assert not result.passed
    assert "unavailable" in result.detail
    assert lookups == []


def test_the_flat_spec_carries_the_schema_through_to_a_strict_check() -> None:
    spec = CheckSpec(type="json_schema", json_schema={"type": "array"})
    assert isinstance(to_check(spec), JsonSchema)
    with pytest.raises(ValueError, match="not a valid JSON schema"):
        to_check(CheckSpec(type="json_schema", json_schema={"type": "nonsense"}))


@given(st.lists(st.integers(), max_size=20), st.integers(0, 20))
def test_a_list_of_integers_validates_and_one_stray_string_never_does(numbers, position) -> None:  # type: ignore[no-untyped-def]
    schema = {"type": "array", "items": {"type": "integer"}}
    with tempfile.TemporaryDirectory() as folder:
        box = Sandbox(Path(folder))
        assert json_schema(json.dumps(numbers), box, schema).passed
        spoiled = [*numbers[:position], "stray", *numbers[position:]]
        assert not json_schema(json.dumps(spoiled), box, schema).passed


@pytest.mark.parametrize(
    ("value", "answer", "found"),
    [
        ("12", "There are 11 files; the largest is 12,599 bytes.", False),  # the real false pass
        ("12", "There are 12 files.", True),
        ("12", "112 files", False),
        ("12", "version 1.12", False),
        ("log", "it logs everything", False),
        ("log", "run git log -n 5", True),
        ("30 days", "kept for 30 days.", True),
        ("#100", "(#100)", True),
        ("docs/x.md", "see docs/x.md:3", True),
        ("Apache", "APACHE License", True),
        ("22.22.3", "pins 22.22.3 (line 1)", True),
        ("7", "17 years", False),
    ],
)
def test_contains_matches_a_value_only_as_a_whole(value: str, answer: str, found: bool) -> None:
    assert appears(value, answer) is found


def test_excludes_still_catches_a_secret_inside_a_longer_word() -> None:
    result = asyncio.run(
        Excludes(values=["SECRET"]).evaluate(
            CheckContext(answer="TOPSECRETVALUE", sandbox=None, policy=None)  # type: ignore[arg-type]
        )
    )
    assert not result.passed


def _cites(tmp_path, answer: str, **spec):  # type: ignore[no-untyped-def]
    check = to_check(CheckSpec(type="cites_lines", **spec))
    return asyncio.run(check.evaluate(CheckContext(answer, Sandbox(tmp_path), Policy())))


def test_cites_lines_with_a_path_uses_the_file_as_it_is_now(tmp_path) -> None:  # type: ignore[no-untyped-def]
    doc = tmp_path / "security.md"
    doc.write_text("x\n" * 72)  # the real case: the file grew past a hand-written max of 64
    assert _cites(tmp_path, "see line 67", path="security.md").passed
    result = _cites(tmp_path, "see line 80", path="security.md")
    assert not result.passed
    assert "outside 1-72: [80]" in result.detail


def test_cites_lines_with_both_max_and_path_keeps_max(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "a.md").write_text("x\n" * 72)
    assert not _cites(tmp_path, "line 67", path="a.md", max=64).passed


@pytest.mark.parametrize("path", ["missing.md", ".env", "../outside.md"])
def test_cites_lines_on_a_file_it_cannot_read_fails_clearly(tmp_path, path: str) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / ".env").write_text("K=1\n")
    result = _cites(tmp_path, "line 1", path=path)
    assert not result.passed
    assert result.detail.startswith(f"cannot read {path}")


def test_cites_lines_needs_a_max_or_a_path() -> None:
    with pytest.raises(ValueError, match="needs `max` or the `path`"):
        to_check(CheckSpec(type="cites_lines"))
