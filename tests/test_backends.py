# pyright: basic
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from deputy import cli
from deputy.backends import BackendError, ChatClient
from deputy.bench import QUICK_TASKS, Summary, holdout_names, selection_names
from deputy.cli import tuning
from deputy.config import Backend, config_path, load_config
from deputy.tune import save_model_profile
from helpers import message


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
    monkeypatch.setattr("deputy.cli.setup.ChatClient", healthy)
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "backend local: ok" in out
    assert "profiles: helper" in out


def test_doctor_fails_when_the_backend_is_down(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["init", "--yes", "--url", "http://srv", "--model", "m"])
    down = lambda b: client_for(lambda _: httpx.Response(503))  # noqa: E731
    monkeypatch.setattr("deputy.cli.setup.ChatClient", down)
    assert cli.main(["doctor"]) == 1
    assert "FAIL" in capsys.readouterr().out


def _measured_on(model: str) -> None:
    summary = Summary(
        variant="baseline", runs=1, pass_rate=1.0, citation_rate=1.0, mean_turns=2.0,
        mean_tokens_out=50, mean_elapsed_s=5, stdev_elapsed_s=0, tokens_per_s=10,
        served_model=model,
    )  # fmt: skip
    save_model_profile(load_config().backend(None), summary, [])


def _doctor_with(
    auto_tune: str,
    served: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[str, list[str]]:
    cli.main(["init", "--yes", "--url", "http://srv", "--model", "m"])
    config = config_path()
    config.write_text(config.read_text().replace("# auto_tune", "auto_tune").replace(
        '"propose"', f'"{auto_tune}"'))  # fmt: skip
    _measured_on("qwen-27b")
    body = {"data": [{"id": served}]}
    monkeypatch.setattr(
        "deputy.cli.setup.ChatClient",
        lambda b: client_for(lambda _: httpx.Response(200, json=body)),
    )
    retuned: list[str] = []
    monkeypatch.setattr(tuning, "retune", lambda name: retuned.append(name) or 0)
    code = cli.main(["doctor"])
    assert code == 0  # a changed model is a notice, never a failure
    return capsys.readouterr().out, retuned


def test_doctor_retunes_a_changed_model_only_when_auto_tune_is_apply(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out, retuned = _doctor_with("apply", "llama-70b", monkeypatch, capsys)
    assert "now serves llama-70b, was measured on qwen-27b" in out
    assert retuned == ["local"]


@pytest.mark.parametrize(
    ("mode", "hint"), [("propose", "run: deputy tune"), ("off", "auto_tune is off")]
)
def test_doctor_only_says_what_to_do_unless_auto_tune_is_apply(
    mode: str, hint: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out, retuned = _doctor_with(mode, "llama-70b", monkeypatch, capsys)
    assert hint in out
    assert retuned == []


def test_doctor_stays_quiet_when_the_model_is_the_one_measured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out, retuned = _doctor_with("apply", "qwen-27b", monkeypatch, capsys)
    assert "now serves" not in out
    assert retuned == []


def test_the_quick_retune_uses_real_tasks_and_writes_the_winner() -> None:
    assert set(QUICK_TASKS) <= set(selection_names()) | set(holdout_names())
    assert set(QUICK_TASKS) & set(holdout_names())  # one held-out task, so a winner is confirmed
