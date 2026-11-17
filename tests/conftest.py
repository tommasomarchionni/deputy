# pyright: basic
"""Shared fixtures: isolated config/state, and a scripted fake model server."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from deputy.backends import ChatClient
from deputy.config import Backend


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPUTY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DEPUTY_STATE", str(tmp_path / "state"))
    monkeypatch.delenv("DEPUTY_CONFIG", raising=False)


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


@pytest.fixture
def fake_client() -> Callable[[list[dict[str, Any] | int]], ChatClient]:
    """Build a ChatClient whose server answers with the given scripted replies, in order.
    An int in the script is an HTTP error status."""

    def build(script: list[dict[str, Any] | int]) -> ChatClient:
        replies = iter(script)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            item = next(replies)
            if isinstance(item, int):
                return httpx.Response(item, json={"error": "boom"})
            return httpx.Response(200, json=item)

        backend = Backend(name="fake", base_url="http://fake", model="fake-model")
        return ChatClient(backend, httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    return build
