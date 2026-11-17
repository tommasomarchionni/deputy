# pyright: basic
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from conftest import message
from deputy import cli
from deputy.backends import BackendError, ChatClient
from deputy.config import Backend


def client_for(handler, **backend_kw) -> ChatClient:  # type: ignore[no-untyped-def]
    backend = Backend(name="b", base_url="http://srv", model="m", **backend_kw)
    return ChatClient(backend, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_chat_sends_the_key_the_tools_and_extra_body(tmp_path: Path) -> None:
    key = tmp_path / "k"
    key.write_text("sekret")
    key.chmod(0o600)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=message("hi"))

    client = client_for(handler, api_key_file=str(key), extra_body={"temperature": 0.2})
    reply = asyncio.run(client.chat([{"role": "user", "content": "x"}], [{"type": "function"}]))
    assert reply.content == "hi"
    assert (reply.tokens_in, reply.tokens_out) == (100, 20)
    assert seen["auth"] == "Bearer sekret"
    assert seen["body"]["temperature"] == 0.2
    assert seen["body"]["tools"] == [{"type": "function"}]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={}),
        httpx.Response(200, json={"nope": 1}),
        httpx.Response(200, text="x"),
    ],
)
def test_bad_answers_are_backend_errors(response: httpx.Response) -> None:
    client = client_for(lambda _: response)
    with pytest.raises(BackendError):
        asyncio.run(client.chat([{"role": "user", "content": "x"}]))


def test_health_reports_ok_refused_key_and_unreachable() -> None:
    ok = client_for(lambda _: httpx.Response(200, json={"data": [{"id": "qwen"}]}))
    assert asyncio.run(ok.health()) == (True, "ok, models: qwen")
    refused = client_for(lambda _: httpx.Response(401))
    assert asyncio.run(refused.health())[1] == "reachable, but the API key was refused"
    other = client_for(lambda _: httpx.Response(503))
    assert asyncio.run(other.health()) == (False, "HTTP 503")

    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert asyncio.run(client_for(boom).health())[1].startswith("unreachable")


def test_doctor_reports_a_healthy_backend(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init", "--yes", "--url", "http://srv", "--model", "m"])
    healthy = lambda b: client_for(lambda _: httpx.Response(200, json={"data": []}))  # noqa: E731
    monkeypatch.setattr(cli, "ChatClient", healthy)
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "backend local: ok" in out
    assert "profiles: helper" in out


def test_doctor_fails_when_the_backend_is_down(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init", "--yes", "--url", "http://srv", "--model", "m"])
    down = lambda b: client_for(lambda _: httpx.Response(503))  # noqa: E731
    monkeypatch.setattr(cli, "ChatClient", down)
    assert cli.main(["doctor"]) == 1
    assert "FAIL" in capsys.readouterr().out
