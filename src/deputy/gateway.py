"""A small local gateway that lets Claude Code's agent loop run on a local model.

Claude Code speaks the Anthropic Messages API. llama.cpp serves that API too, but rejects some of
what Claude Code sends, so requests are reduced to what the model server understands. The gateway
listens on the loopback address, answers only to a random token and forwards to one backend.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import socket
import threading
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from deputy.config import Backend

MAX_TOKENS = 8_192
REPLY_TIMEOUT_S = 3_600.0  # a local model can take long over one long reply
_START_WAIT_S = 10.0

Json = dict[str, Any]


def _without_cache_marks(value: Any) -> Any:
    if isinstance(value, dict):
        items = cast("Json", value).items()
        return {k: _without_cache_marks(v) for k, v in items if k != "cache_control"}
    if isinstance(value, list):
        return [_without_cache_marks(item) for item in cast("list[Any]", value)]
    return value


def _system_text(system: Any) -> str:
    if isinstance(system, list):
        blocks = cast("list[Json]", system)
        return "\n\n".join(str(block.get("text", "")) for block in blocks)
    return str(system or "")


def _flat_tool_result(block: Json) -> Json:
    content = block.get("content")
    if not isinstance(content, list):
        return block
    parts = cast("list[Json]", content)
    text = "\n".join(
        str(p.get("text", "")) if p.get("type") == "text" else f"[{p.get('type')}]" for p in parts
    )
    return {**block, "content": text}


def _message(message: Json) -> Json | None:
    content = message.get("content")
    if not isinstance(content, list):
        return message
    kept = [
        _flat_tool_result(block) if block.get("type") == "tool_result" else block
        for block in cast("list[Json]", content)
        if block.get("type") not in ("thinking", "redacted_thinking")
    ]
    return {**message, "content": kept} if kept else None


def normalise(request: Json, backend: Backend) -> Json:
    """The request reduced to what a local model server accepts."""
    raw = cast("list[Json]", request.get("messages") or [])
    extra = [str(m.get("content")) for m in raw if m.get("role") == "system"]
    messages = [m for m in (_message(x) for x in raw if x.get("role") != "system") if m]
    body: Json = {
        "model": backend.model,
        "max_tokens": min(int(request.get("max_tokens") or MAX_TOKENS), MAX_TOKENS),
        "stream": bool(request.get("stream")),
        "system": "\n\n".join([_system_text(request.get("system")), *extra]).strip(),
        "messages": _without_cache_marks(messages),
        **backend.extra_body,
    }
    tools = [
        {key: tool[key] for key in ("name", "description", "input_schema") if key in tool}
        for tool in cast("list[Json]", request.get("tools") or [])
        if "input_schema" in tool
    ]
    if tools:
        body["tools"] = _without_cache_marks(tools)
    if request.get("tool_choice"):
        body["tool_choice"] = request["tool_choice"]
    return body


def create_app(backend: Backend, token: str, transport: httpx.AsyncBaseTransport | None = None):
    """The gateway application; `transport` replaces the network in tests."""

    def allowed(request: Request) -> bool:
        given = request.headers.get("authorization", "").removeprefix("Bearer ")
        return secrets.compare_digest(given or request.headers.get("x-api-key", ""), token)

    async def messages(request: Request) -> Response:
        if not allowed(request):
            return JSONResponse({"error": {"message": "invalid token"}}, status_code=401)
        if request.url.path.endswith("/count_tokens"):
            return JSONResponse({"input_tokens": len(await request.body()) // 4})
        body = normalise(cast("Json", json.loads(await request.body())), backend)
        key = backend.api_key()
        headers = {"anthropic-version": "2023-06-01", **({"x-api-key": key} if key else {})}
        if key:
            headers["authorization"] = f"Bearer {key}"
        client = httpx.AsyncClient(timeout=REPLY_TIMEOUT_S, transport=transport)
        url = f"{backend.base_url.rstrip('/')}/v1/messages"
        upstream = await client.send(
            client.build_request("POST", url, headers=headers, json=body), stream=True
        )

        async def relay() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        kind = upstream.headers.get("content-type", "application/json")
        return StreamingResponse(relay(), status_code=upstream.status_code, media_type=kind)

    return Starlette(routes=[Route("/v1/messages{rest:path}", messages, methods=["POST"])])


class Gateway:
    """The gateway running on a free loopback port for as long as the `with` block lasts."""

    def __init__(self, backend: Backend) -> None:
        self.token = secrets.token_urlsafe(32)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = int(probe.getsockname()[1])
        config = uvicorn.Config(
            create_app(backend, self.token), host="127.0.0.1", port=self.port, log_level="warning"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def __aenter__(self) -> Gateway:
        self._thread.start()
        for _ in range(int(_START_WAIT_S / 0.05)):
            if self._server.started:
                return self
            await asyncio.sleep(0.05)
        raise RuntimeError("the local gateway did not start")

    async def __aexit__(self, *_: object) -> None:
        self._server.should_exit = True
        await asyncio.to_thread(self._thread.join, _START_WAIT_S)
