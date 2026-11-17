# pyright: basic
"""Property tests: the guarantees that must hold for ANY input, not only the ones we thought of."""

import tomllib
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from deputy.checks import cited_numbers
from deputy.sandbox import Sandbox, SandboxError
from deputy.tune import _toml

_NO_FIXTURE_CHECK = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])


@_NO_FIXTURE_CHECK
@given(st.text(max_size=80))
def test_no_path_ever_resolves_outside_the_sandbox(tmp_path: Path, path: str) -> None:
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    box = Sandbox(root)
    try:
        resolved = box.resolve(path)
    except SandboxError:
        return
    assert resolved == box.root or box.root in resolved.parents


@_NO_FIXTURE_CHECK
@given(st.lists(st.sampled_from(["..", ".", "a", "b", "", "/"]), max_size=8))
def test_dot_dot_chains_cannot_escape(tmp_path: Path, parts: list[str]) -> None:
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    box = Sandbox(root)
    try:
        resolved = box.resolve("/".join(parts))
    except SandboxError:
        return
    assert resolved == box.root or box.root in resolved.parents


@given(st.text(max_size=200))
def test_the_citation_parser_never_raises_and_returns_integers(answer: str) -> None:
    assert all(isinstance(n, int) and n >= 0 for n in cited_numbers(answer))


@given(st.integers(min_value=1, max_value=10_000), st.integers(min_value=1, max_value=10_000))
def test_cited_ranges_are_found_in_both_forms(first: int, second: int) -> None:
    assert first in cited_numbers(f"see lines {first}-{second}")
    assert second in cited_numbers(f"see src/a.py:{first}-{second}")


_VALUES = st.recursive(
    st.one_of(
        st.booleans(),
        st.integers(min_value=-(2**31), max_value=2**31),
        st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=20),
    ),
    lambda children: (
        st.dictionaries(
            st.text(alphabet="abcXYZ_-1 ", min_size=1, max_size=8), children, max_size=3
        )
        | st.lists(children, max_size=3)
    ),
    max_leaves=8,
)


@given(st.dictionaries(st.text(alphabet="abc_", min_size=1, max_size=6), _VALUES, max_size=4))
def test_the_toml_writer_round_trips_through_a_real_parser(table: dict[str, object]) -> None:
    text = "value = " + _toml(table)
    assert tomllib.loads(text)["value"] == table
