# pyright: basic
from pathlib import Path

import pytest

from deputy.agent import TOOLS
from deputy.config import config_dir
from deputy.profiles import load_profiles, overrides, parse_profile


def test_front_matter_is_parsed() -> None:
    p = parse_profile("---\nname: x\ndescription: d\n---\n\nBody here\n", "t", "fallback")
    assert (p.name, p.description, p.body) == ("x", "d", "Body here")


def test_no_front_matter_uses_the_file_name() -> None:
    assert parse_profile("just text", "t", "stem").name == "stem"


def test_bundled_profile_exists() -> None:
    assert "helper" in load_profiles()


def test_project_profiles_override_user_and_bundled(tmp_path: Path) -> None:
    user = config_dir() / "profiles"
    user.mkdir(parents=True)
    (user / "helper.md").write_text("---\nname: helper\ndescription: user\n---\nu")
    project = tmp_path / ".deputy" / "profiles"
    project.mkdir(parents=True)
    (project / "helper.md").write_text("---\nname: helper\ndescription: project\n---\np")
    assert load_profiles()["helper"].description == "user"
    assert load_profiles(tmp_path)["helper"].description == "project"


@pytest.mark.parametrize(
    ("front", "turns", "retries", "problems"),
    [
        ("max_turns: 12\nmax_retries: 0", 12, 0, 0),
        ("max_turns: 1\nmax_retries: 5", 1, 5, 0),
        ("", None, None, 0),
        ("max_turns: many", None, None, 1),
        ("max_turns: 0\nmax_retries: 9", None, None, 2),
        ("max_retries: -1", None, None, 1),
    ],
)
def test_a_profile_can_set_its_limits_and_a_bad_value_is_ignored_with_a_reason(
    front: str, turns: int | None, retries: int | None, problems: int
) -> None:
    text = f"---\nname: p\ndescription: d\n{front}\n---\nbody"
    profile = parse_profile(text, "test", "p")
    assert (profile.max_turns, profile.max_retries, len(profile.problems)) == (
        turns,
        retries,
        problems,
    )
    assert profile.body == "body"


def test_the_bundled_profile_keeps_the_default_limits() -> None:
    helper = load_profiles()["helper"]
    assert (helper.max_turns, helper.max_retries, helper.problems) == (None, None, ())


def test_the_bundled_profile_names_every_worker_tool() -> None:
    text = load_profiles()["helper"].body
    for tool in TOOLS:
        assert f"`{tool['function']['name']}`" in text


def _write(folder: Path, name: str, front: str, body: str = "own lines") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(f"---\nname: {name}\n{front}\n---\n{body}\n")


def test_the_template_gets_every_worker_tool_from_helper_and_its_limits_are_read(
    tmp_path: Path,
) -> None:
    project = tmp_path / ".deputy" / "profiles"
    project.mkdir(parents=True)
    template = Path(__file__).parents[1] / "examples" / "project-dev.md"
    (project / "project-dev.md").write_text(template.read_text())
    profile = load_profiles(tmp_path)["project-dev"]
    for tool in TOOLS:
        assert f"`{tool['function']['name']}`" in profile.body
    assert (profile.max_turns, profile.max_retries, profile.problems) == (10, 1, ())


def test_a_profile_extends_the_current_text_of_its_parent(tmp_path: Path) -> None:
    _write(tmp_path / ".deputy" / "profiles", "mine", "extends: helper")
    helper, mine = load_profiles(tmp_path)["helper"], load_profiles(tmp_path)["mine"]
    assert mine.body == f"{helper.body}\n\nown lines"
    assert mine.problems == ()


def test_limits_come_from_the_child_when_set_else_from_the_parent(tmp_path: Path) -> None:
    folder = tmp_path / ".deputy" / "profiles"
    _write(folder, "base", "description: base\nmax_turns: 4\nmax_retries: 2", "base lines")
    _write(folder, "child", "extends: base\nmax_retries: 0")
    child = load_profiles(tmp_path)["child"]
    assert (child.max_turns, child.max_retries, child.description) == (4, 0, "base")
    assert child.body == "base lines\n\nown lines"


def test_a_chain_of_parents_is_followed(tmp_path: Path) -> None:
    folder = tmp_path / ".deputy" / "profiles"
    _write(folder, "a", "", "a lines")
    _write(folder, "b", "extends: a", "b lines")
    _write(folder, "c", "extends: b", "c lines")
    assert load_profiles(tmp_path)["c"].body == "a lines\n\nb lines\n\nc lines"


def test_a_profile_that_extends_its_own_name_builds_on_the_layer_below(tmp_path: Path) -> None:
    bundled = load_profiles()["helper"].body
    _write(tmp_path / ".deputy" / "profiles", "helper", "extends: helper")
    profiles = load_profiles(tmp_path)
    assert profiles["helper"].body == f"{bundled}\n\nown lines"
    assert overrides(profiles) == []  # it stays current, so doctor has nothing to say


def test_a_parent_replaced_in_a_higher_layer_is_the_one_extended(tmp_path: Path) -> None:
    _write(config_dir() / "profiles", "mine", "extends: base", "mine lines")
    _write(config_dir() / "profiles", "base", "", "user base")
    _write(tmp_path / ".deputy" / "profiles", "base", "", "project base")
    assert load_profiles(tmp_path)["mine"].body == "project base\n\nmine lines"


@pytest.mark.parametrize(
    ("files", "name", "problem"),
    [
        ({"a": "extends: nobody"}, "a", "no profile named 'nobody'"),
        ({"a": "extends: b", "b": "extends: a"}, "a", "leads back to this profile"),
        ({"a": "extends: a"}, "a", "no profile named 'a'"),
    ],
)
def test_a_bad_parent_is_reported_and_the_profile_still_works(
    tmp_path: Path, files: dict[str, str], name: str, problem: str
) -> None:
    for file, front in files.items():
        _write(tmp_path / ".deputy" / "profiles", file, front)
    profile = load_profiles(tmp_path)[name]
    assert "own lines" in profile.body
    assert any(problem in p for p in profile.problems)


def test_a_trailing_comment_does_not_hide_a_limit() -> None:
    text = "---\nname: x\nmax_turns: 10   # the most turns\nmax_retries: 1 # rounds\n---\nbody"
    profile = parse_profile(text, "t", "x")
    assert (profile.max_turns, profile.max_retries, profile.problems) == (10, 1, ())


def test_a_copy_that_replaces_a_bundled_profile_is_reported(tmp_path: Path) -> None:
    user = config_dir() / "profiles"
    user.mkdir(parents=True, exist_ok=True)
    (user / "helper.md").write_text("---\nname: helper\ndescription: an old copy\n---\nold body\n")
    (user / "mine.md").write_text("---\nname: mine\ndescription: my own\n---\nbody\n")
    warnings = overrides(load_profiles())
    assert len(warnings) == 1
    assert warnings[0].startswith("profile helper: your user copy replaces the bundled one")


def test_no_warning_without_copies() -> None:
    assert overrides(load_profiles()) == []
