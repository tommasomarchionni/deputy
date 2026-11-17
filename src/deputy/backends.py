"""Talk to a worker model over the OpenAI-compatible chat API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from deputy.config import Backend


class BackendError(Exception):
    """The model server failed or answered something unusable."""


@dataclass(frozen=True)
class Reply:
    content: str
    tool_calls: list[dict[str, Any]]
    tokens_in: int
    tokens_out: int
    model: str


class ChatClient:
    def __init__(self, backend: Backend, client: httpx.AsyncClient | None = None) -> None:
        self.backend = backend
        self._client = client or httpx.AsyncClient(timeout=backend.timeout_s)

    def _headers(self) -> dict[str, str]:
        key = self.backend.api_key()
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> Reply:
        body: dict[str, Any] = {
            "model": self.backend.model,
            "messages": messages,
            "max_tokens": self.backend.max_tokens,
            **self.backend.extra_body,
        }
        if tools:
            body["tools"] = tools
        url = self.backend.base_url.rstrip("/") + "/v1/chat/completions"
        try:
            response = await self._client.post(url, json=body, headers=self._headers())
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            message: dict[str, Any] = data["choices"][0]["message"]
        except httpx.HTTPStatusError as exc:
            raise BackendError(f"HTTP {exc.response.status_code} from {url}") from exc
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise BackendError(f"unusable answer from {url}: {type(exc).__name__}") from exc
        usage: dict[str, Any] = data.get("usage") or {}
        return Reply(
            content=message.get("content") or "",
            tool_calls=list(message.get("tool_calls") or []),
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            model=str(data.get("model") or self.backend.model),
        )

    async def health(self) -> tuple[bool, str]:
        """Reachability and the model list, as far as the server tells."""
        base = self.backend.base_url.rstrip("/")
        try:
            response = await self._client.get(f"{base}/v1/models", headers=self._headers())
        except httpx.HTTPError as exc:
            return False, f"unreachable: {type(exc).__name__}"
        if response.status_code == 401:
            return False, "reachable, but the API key was refused"
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        try:
            models = [str(m.get("id")) for m in response.json().get("data", [])]
        except ValueError:
            models = []
        return True, "ok" + (f", models: {', '.join(models[:3])}" if models else "")

    async def aclose(self) -> None:
        await self._client.aclose()
