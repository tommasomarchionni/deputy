"""Configuration: where the backends are and what the policy allows. Never holds a secret inline."""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ConfigError(Exception):
    """The configuration is missing or invalid. The message tells the user what to fix."""


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(os.environ.get("DEPUTY_HOME", Path(base) / "deputy"))


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(os.environ.get("DEPUTY_STATE", Path(base) / "deputy"))


class Backend(BaseModel):
    """An OpenAI-compatible chat endpoint (llama.cpp, Ollama, vLLM, LM Studio, hosted APIs)."""

    name: str
    base_url: str = Field(description="Server root, e.g. http://127.0.0.1:8080")
    model: str = Field(default="default", description="Model name sent in each request")
    api_key_file: str | None = None
    api_key_env: str | None = None
    timeout_s: float = 600.0
    max_tokens: int = 4096
    kind: Literal["local", "hosted"] = "local"
    extra_body: dict[str, object] = Field(default_factory=dict[str, object])

    @model_validator(mode="after")
    def _one_key_source(self) -> Backend:
        if self.api_key_file and self.api_key_env:
            raise ValueError("use either api_key_file or api_key_env, not both")
        return self

    def api_key(self) -> str | None:
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        if self.api_key_file:
            path = Path(self.api_key_file).expanduser()
            if not path.is_file():
                raise ConfigError(f"api_key_file not found: {path}")
            if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise ConfigError(f"{path} is readable by others; run: chmod 600 {path}")
            return path.read_text(encoding="utf-8").strip()
        return None


class Policy(BaseModel):
    """What a task may ask the server to run. Empty by default: no command runs unless listed."""

    allowed_commands: list[list[str]] = Field(
        default_factory=list[list[str]],
        description="argv prefixes a `command` check may run, e.g. [['node','--test']]",
    )
    command_timeout_s: float = 120.0
    command_wrapper: list[str] = Field(
        default_factory=list[str],
        description="argv prefix that confines every `command` check; {workdir} is replaced",
    )


class Config(BaseModel):
    default_backend: str
    backends: list[Backend]
    auto_tune: Literal["off", "propose", "apply"] = "propose"
    auto_update: Literal["off", "notify", "apply"] = "notify"
    policy: Policy = Field(default_factory=Policy)

    def backend(self, name: str | None = None) -> Backend:
        wanted = name or self.default_backend
        for backend in self.backends:
            if backend.name == wanted:
                return backend
        known = ", ".join(b.name for b in self.backends) or "none"
        raise ConfigError(f"unknown backend '{wanted}' (configured: {known})")


def config_path() -> Path:
    return Path(os.environ.get("DEPUTY_CONFIG", config_dir() / "config.toml"))


def load_config(path: Path | None = None) -> Config:
    target = path or config_path()
    if not target.is_file():
        raise ConfigError(f"no configuration at {target}; run: deputy init")
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
        config = Config.model_validate(data)
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        raise ConfigError(f"invalid configuration {target}: {exc}") from exc
    from deputy.tune import merge_tuned, read_tuned  # noqa: PLC0415  (tune imports this module)

    tuned = read_tuned()
    config.backends = [merge_tuned(b, tuned) for b in config.backends]
    return config
