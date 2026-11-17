"""Apply, keep and undo measured settings. Writes only `tuned.toml`, never the user config."""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any, cast

from deputy.bench import Summary, Variant
from deputy.config import Backend, config_dir, state_dir


def tuned_path() -> Path:
    return config_dir() / "tuned.toml"


def read_tuned() -> dict[str, dict[str, Any]]:
    """Overrides per backend name, from tuned.toml. A broken file is ignored, not fatal."""
    path = tuned_path()
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}
    backends = data.get("backends", {})
    return {str(k): dict(v) for k, v in backends.items() if isinstance(v, dict)}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def merge_tuned(backend: Backend, tuned: dict[str, dict[str, Any]]) -> Backend:
    override = tuned.get(backend.name)
    if not override:
        return backend
    update: dict[str, Any] = {}
    if isinstance(override.get("extra_body"), dict):
        update["extra_body"] = {**backend.extra_body, **override["extra_body"]}
    if isinstance(override.get("max_tokens"), int):
        update["max_tokens"] = override["max_tokens"]
    return backend.model_copy(update=update)


def _toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, dict):
        mapping = cast("dict[Any, Any]", value)
        items = ", ".join(f"{_key(str(k))} = {_toml(v)}" for k, v in mapping.items())
        return "{ " + items + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in cast("list[Any]", value)) + "]"
    return json.dumps(str(value))


def _key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def write_tuned(backend: str, variant: Variant) -> Path:
    """Record the winning request-level settings. The previous file is kept as tuned.toml.prev."""
    path = tuned_path()
    current = read_tuned()
    if path.is_file():
        shutil.copyfile(path, path.with_suffix(".toml.prev"))
    entry: dict[str, Any] = {}
    if variant.extra_body:
        entry["extra_body"] = variant.extra_body
    if variant.max_tokens:
        entry["max_tokens"] = variant.max_tokens
    current[backend] = entry
    lines = [
        "# Written by `deputy tune`. Delete this file, or run `deputy tune --revert`, to undo."
    ]
    for name, values in current.items():
        lines.append(f"\n[backends.{_key(name)}]")
        lines.extend(f"{_key(k)} = {_toml(v)}" for k, v in values.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def revert_tuned() -> str:
    path = tuned_path()
    previous = path.with_suffix(".toml.prev")
    if previous.is_file():
        shutil.copyfile(previous, path)
        previous.unlink()
        return f"restored the previous {path.name}"
    if path.is_file():
        path.unlink()
        return f"removed {path.name}; back to your own configuration"
    return "nothing to revert"


def save_model_profile(backend: Backend, baseline: Summary, others: list[Summary]) -> Path:
    """The measured profile of this model on this server, for routing and for comparison."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{backend.name}__{backend.model}")
    path = state_dir() / "models" / f"{safe}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "backend": backend.name,
                "model": backend.model,
                "baseline": baseline.model_dump(),
                "variants": [s.model_dump() for s in others],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
