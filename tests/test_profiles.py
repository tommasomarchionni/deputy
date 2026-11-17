# pyright: basic
from pathlib import Path

from deputy.config import config_dir
from deputy.profiles import load_profiles, parse_profile


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
