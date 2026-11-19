# pyright: basic
import asyncio
import json
from typing import Any

import httpx

from deputy.config import Backend
from deputy.gateway import MAX_TOKENS, create_app, first_change, normalise

BACKEND = Backend(name="b", base_url="http://model", model="local-model")


def test_a_request_is_reduced_to_what_the_model_server_accepts() -> None:
    body = normalise(
        {
            "model": "claude",
            "max_tokens": 64_000,
            "system": [{"type": "text", "text": "one", "cache_control": {"type": "ephemeral"}}],
            "messages": [
                {"role": "system", "content": "two"},
                {"role": "assistant", "content": [{"type": "thinking", "thinking": "hm"}]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "a",
                            "cache_control": {"type": "ephemeral"},
                            "content": [{"type": "text", "text": "out"}, {"type": "image"}],
                        }
                    ],
                },
            ],
            "tools": [
                {"name": "Read", "description": "d", "input_schema": {}, "defer_loading": True},
                {"type": "web_search", "name": "server side"},
            ],
        },
        BACKEND,
    )
    assert body["model"] == "local-model"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["max_tokens"] == MAX_TOKENS
    assert body["system"] == "one"
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "two"}]},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "a", "content": "out\n[image]"}],
        },
    ]
    assert body["tools"] == [{"name": "Read", "description": "d", "input_schema": {}}]


def test_system_messages_stay_where_they_are_in_the_conversation() -> None:
    # Claude Code sends system messages inside `messages`, first with a list of blocks and then
    # as a string; moved to the system prompt they changed its start and forced a full re-read
    def request(*messages: dict[str, Any]) -> dict[str, Any]:
        return normalise({"system": "rules", "messages": list(messages)}, BACKEND)

    first = {"role": "user", "content": [{"type": "text", "text": "task"}]}
    turn = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "a", "name": "Bash", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "a", "content": "out"}],
        },
    ]
    one = request(first, {"role": "system", "content": [{"type": "text", "text": "env"}]})
    two = request(first, {"role": "system", "content": "env"}, *turn)
    assert one["system"] == two["system"] == "rules"
    assert one["messages"] == two["messages"][:1]
    assert two["messages"][0]["content"][-1] == {"type": "text", "text": "env"}
    three = request(
        first, {"role": "system", "content": "env"}, *turn, {"role": "system", "content": "late"}
    )
    assert three["messages"][:2] == two["messages"][:2]
    assert three["messages"][2]["content"][0]["content"] == "out\n\nlate"


def _post(app: object, headers: dict[str, str], path: str = "/v1/messages") -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            return await client.post(path, headers=headers, json={"messages": []})

    return asyncio.run(go())


def test_the_gateway_answers_only_to_its_token() -> None:
    app = create_app(BACKEND, "secret", httpx.MockTransport(lambda _: httpx.Response(500)))
    assert _post(app, {}).status_code == 401
    assert _post(app, {"authorization": "Bearer wrong"}).status_code == 401


def test_the_gateway_forwards_to_the_backend_with_its_own_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_KEY", "model-key")
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b'{"ok": true}'))

    backend = BACKEND.model_copy(update={"api_key_env": "LOCAL_KEY"})
    reply = _post(
        create_app(backend, "secret", httpx.MockTransport(upstream)), {"x-api-key": "secret"}
    )
    assert reply.json() == {"ok": True}
    assert str(seen[0].url) == "http://model/v1/messages"
    assert seen[0].headers["authorization"] == "Bearer model-key"
    assert json.loads(seen[0].content)["model"] == "local-model"


def test_counting_tokens_needs_no_model() -> None:
    app = create_app(BACKEND, "secret", httpx.MockTransport(lambda _: httpx.Response(500)))
    reply = _post(app, {"x-api-key": "secret"}, "/v1/messages/count_tokens")
    assert reply.status_code == 200
    assert reply.json()["input_tokens"] > 0


def test_the_first_change_between_two_requests_is_located() -> None:
    base = {"system": "s", "tools": [], "messages": [{"role": "user", "content": "a"}]}
    longer = {**base, "messages": [*base["messages"], {"role": "assistant", "content": "b"}]}
    edited = {**base, "messages": [{"role": "user", "content": [{"type": "tool_result"}]}]}
    assert first_change(None, base)["where"] == "start"
    assert first_change(base, longer) == {
        "messages": 2,
        "first_changed": 1,
        "of": 1,
        "where": "appended",
    }
    assert first_change(base, edited)["where"] == "user:tool_result"
    assert first_change(base, {**base, "system": "t"})["where"] == "system"


def test_notes_next_to_tool_results_go_inside_the_last_result() -> None:
    body = normalise(
        {
            "messages": [
                {"role": "user", "content": "the task"},
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "a", "content": "one"},
                        {"type": "tool_result", "tool_use_id": "b", "content": "two"},
                        {"type": "text", "text": "<system-reminder>note</system-reminder>"},
                    ],
                },
                {"role": "user", "content": [{"type": "text", "text": "a real question"}]},
            ]
        },
        BACKEND,
    )
    turn = body["messages"][1]["content"]
    assert [b["type"] for b in turn] == ["tool_result", "tool_result"]
    assert turn[1]["content"] == "two\n\n<system-reminder>note</system-reminder>"
    assert body["messages"][2]["content"] == [{"type": "text", "text": "a real question"}]


def test_a_backend_can_turn_reasoning_back_on() -> None:
    backend = BACKEND.model_copy(
        update={"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}
    )
    body = normalise({"messages": []}, backend)
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
