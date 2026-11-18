"""Smaller tool schemas: the orchestrator pays for every character on every turn."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict


def _drop_property_titles(schema: dict[str, Any]) -> None:
    """Remove the generated `title` of each property (it only repeats the property name)."""
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)


COMPACT = ConfigDict(json_schema_extra=_drop_property_titles)
