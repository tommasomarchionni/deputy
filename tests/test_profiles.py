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


def test_the_template_profile_names_every_worker_tool() -> None:
    text = (Path(__file__).parents[1] / "examples" / "project-dev.md").read_text()
    for tool in TOOLS:
        assert f"`{tool['function']['name']}`" in text


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
