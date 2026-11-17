# pyright: basic
import os
from pathlib import Path

import pytest

from deputy.config import Backend, ConfigError, load_config

TOML = """
default_backend = "local"
[[backends]]
name = "local"
base_url = "http://127.0.0.1:8080"
model = "m"
{key}
"""


def write(tmp_path: Path, key: str = "") -> Path:
    path = tmp_path / "config.toml"
    path.write_text(TOML.format(key=key))
    return path


def test_loads_a_valid_config(tmp_path: Path) -> None:
    config = load_config(write(tmp_path))
    assert config.backend().model == "m"
    assert config.policy.allowed_commands == []


def test_a_missing_config_tells_the_user_what_to_run(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="deputy init"):
        load_config(tmp_path / "none.toml")


def test_an_invalid_config_is_a_clear_error(tmp_path: Path) -> None:
    bad = tmp_path / "config.toml"
    bad.write_text("default_backend = 1")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_unknown_backend_lists_the_known_ones(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="local"):
        load_config(write(tmp_path)).backend("other")


def test_key_file_must_not_be_readable_by_others(tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_text("secret\n")
    key.chmod(0o644)
    backend = Backend(name="b", base_url="http://x", api_key_file=str(key))
    with pytest.raises(ConfigError, match="chmod 600"):
        backend.api_key()
    key.chmod(0o600)
    assert backend.api_key() == "secret"


def test_key_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_KEY", "abc")
    assert Backend(name="b", base_url="http://x", api_key_env="MY_KEY").api_key() == "abc"
    assert Backend(name="b", base_url="http://x").api_key() is None


def test_both_key_sources_are_refused() -> None:
    with pytest.raises(ValueError, match="either"):
        Backend(name="b", base_url="http://x", api_key_env="A", api_key_file="/tmp/b")


def test_a_missing_key_file_is_reported() -> None:
    backend = Backend(name="b", base_url="http://x", api_key_file="/nonexistent/key")
    with pytest.raises(ConfigError, match="not found"):
        backend.api_key()


def test_default_paths_follow_the_environment(tmp_path: Path) -> None:
    assert os.environ["DEPUTY_HOME"].startswith(str(tmp_path))
