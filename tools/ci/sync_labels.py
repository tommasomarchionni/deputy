# pyright: basic
"""Apply .github/labels.json to the repository: rename (`from`), create, update; never delete."""

from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from ghapi import gh, repo

LABELS_FILE = Path(__file__).resolve().parents[2] / ".github" / "labels.json"


def plan(wanted: list[dict[str, Any]], existing: set[str]) -> list[tuple[str, dict[str, Any]]]:
    """Decide the action per label: ('rename'|'update'|'create', label). Pure, so it is testable."""
    actions: list[tuple[str, dict[str, Any]]] = []
    for label in wanted:
        if label["name"] in existing:
            actions.append(("update", label))
        elif label.get("from") in existing:
            actions.append(("rename", label))
        else:
            actions.append(("create", label))
    return actions


def _quote(name: str) -> str:
    return urllib.parse.quote(name, safe="")


def apply(actions: list[tuple[str, dict[str, Any]]]) -> list[str]:
    lines: list[str] = []
    for action, label in actions:
        body = {"color": label["color"], "description": label.get("description", "")}
        if action == "create":
            gh(f"repos/{repo()}/labels", "-X", "POST", payload={"name": label["name"], **body})
        elif action == "rename":
            gh(
                f"repos/{repo()}/labels/{_quote(label['from'])}",
                "-X",
                "PATCH",
                payload={"new_name": label["name"], **body},
            )
        else:
            gh(f"repos/{repo()}/labels/{_quote(label['name'])}", "-X", "PATCH", payload=body)
        lines.append(f"{action}: {label['name']}")
    return lines


def main() -> int:
    wanted = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    existing = {item["name"] for item in gh(f"repos/{repo()}/labels", "--paginate")}
    for line in apply(plan(wanted, existing)):
        print(line)
    extra = sorted(existing - {w["name"] for w in wanted} - {w.get("from") for w in wanted})
    if extra:
        print(f"not in labels.json (left alone): {', '.join(extra)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
