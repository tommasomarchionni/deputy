# pyright: basic
"""A real HTTP server that speaks the OpenAI chat API, so integration tests cross a real socket.

It answers from a script (the replies are queued by the test) and records what it received.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


@dataclass
class FakeModel:
    """State shared between the server thread and the test."""

    replies: list[dict[str, Any] | int] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    required_key: str | None = None
    base_url: str = ""

    def queue(self, *replies: dict[str, Any] | int) -> None:
        self.replies.extend(replies)


def _app(model: FakeModel) -> Starlette:
    async def models(request: Request) -> JSONResponse:
        if (
            model.required_key
            and request.headers.get("authorization") != f"Bearer {model.required_key}"
        ):
            return JSONResponse({"error": "bad key"}, status_code=401)
        return JSONResponse({"data": [{"id": "fake-model"}]})

    async def chat(request: Request) -> JSONResponse:
        model.requests.append(await request.json())
        model.headers.append(dict(request.headers))
        if (
            model.required_key
            and request.headers.get("authorization") != f"Bearer {model.required_key}"
        ):
            return JSONResponse({"error": "bad key"}, status_code=401)
        if not model.replies:
            return JSONResponse({"error": "no scripted reply"}, status_code=500)
        item = model.replies.pop(0)
        if isinstance(item, int):
            return JSONResponse({"error": "scripted failure"}, status_code=item)
        return JSONResponse(item)

    return Starlette(
        routes=[Route("/v1/models", models), Route("/v1/chat/completions", chat, methods=["POST"])]
    )


@contextmanager
def serve(model: FakeModel) -> Iterator[FakeModel]:
    """Run the fake server on a free port for the duration of the block."""
    server = uvicorn.Server(
        uvicorn.Config(_app(model), host="127.0.0.1", port=0, log_level="error", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        thread.join(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    model.base_url = f"http://127.0.0.1:{port}"
    try:
        yield model
    finally:
        server.should_exit = True
        thread.join(5)
