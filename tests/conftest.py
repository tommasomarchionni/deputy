# pyright: basic
"""Shared fixtures: isolated config/state, and a scripted fake model server."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from deputy import updates
from deputy.backends import ChatClient
from deputy.config import Backend
from deputy.version import current_version


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Unit tests never reach GitHub: the update check is answered as 'up to date'.

    test_updates.py tests the real check, with a fake transport, so it is left alone.
    """
    if request.module.__name__ == "test_updates":
        return
    monkeypatch.setattr(updates, "check", lambda **_: updates.UpdateInfo(current_version(), None))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPUTY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DEPUTY_STATE", str(tmp_path / "state"))
    monkeypatch.delenv("DEPUTY_CONFIG", raising=False)


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
