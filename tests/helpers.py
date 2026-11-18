# pyright: basic
"""Builders for the scripted model replies used across the tests."""

import json
from typing import Any


def message(content: str = "", tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "model": "fake-model",
        "choices": [{"message": msg}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


def tool_call(name: str, arguments: dict[str, Any], call_id: str = "c1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
