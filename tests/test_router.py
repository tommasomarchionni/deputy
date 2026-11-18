# pyright: basic
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from deputy import router, server
from deputy.backends import ChatClient
from deputy.bench import Summary
from deputy.config import Backend, Config, config_path, state_dir
from deputy.model_profiles import ModelProfile
from deputy.server import build_server
from deputy.tune import save_model_profile
from helpers import message


def backend(name: str, kind: str = "local") -> Backend:
    return Backend(name=name, base_url=f"http://{name}", kind=kind)  # type: ignore[arg-type]


def config(*backends: Backend, default: str | None = None) -> Config:
    return Config(default_backend=default or backends[0].name, backends=list(backends))


def seen(pass_rate: float, elapsed: float) -> ModelProfile:
    return ModelProfile("x", "m", "m", 0.0, pass_rate, 2.0, elapsed)


def order(cfg: Config, measured: dict[str, ModelProfile] | None = None) -> list[str]:
    found = measured or {}
    return [c.backend.name for c in router.candidates(cfg, lambda b: found.get(b.name))]


def test_a_local_default_never_routes_to_a_hosted_backend() -> None:
    cfg = config(backend("home"), backend("cloud", "hosted"), backend("laptop"))
    assert order(cfg) == ["home", "laptop"]


def test_a_hosted_default_prefers_local_backends_and_may_fall_back_to_itself() -> None:
    cfg = config(backend("cloud", "hosted"), backend("home"), default="cloud")
    assert order(cfg) == ["home", "cloud"]


def test_measured_to_pass_comes_before_not_measured_before_measured_to_fail() -> None:
    cfg = config(backend("a"), backend("b"), backend("c"))
    measured = {"a": seen(0.2, 5), "c": seen(0.9, 50)}
    assert order(cfg, measured) == ["c", "b", "a"]


def test_the_faster_measured_backend_wins_and_ties_go_to_the_default_then_the_order() -> None:
    cfg = config(backend("a"), backend("b"), backend("c"), default="b")
    assert order(cfg, {"a": seen(1, 30), "b": seen(1, 10), "c": seen(1, 10)}) == ["b", "c", "a"]
    assert order(cfg) == ["b", "a", "c"]  # nothing measured: the default, then the order


def test_the_reason_says_what_the_ranking_saw() -> None:
    cfg = config(backend("a"), backend("b"))
    found = {"a": seen(0.9, 12)}
    reasons = {c.backend.name: c.why for c in router.candidates(cfg, lambda b: found.get(b.name))}
    assert reasons == {"a": "a (local, 90% passed, 12s)", "b": "b (local, not measured)"}


@given(
    kinds=st.lists(st.sampled_from(["local", "hosted"]), min_size=1, max_size=6),
    rates=st.lists(st.floats(0, 1), min_size=6, max_size=6),
    default=st.integers(0, 5),
)
def test_the_order_is_a_stable_permutation_that_never_goes_up_in_kind(
    kinds: list[str], rates: list[float], default: int
) -> None:
    backends = [backend(f"b{i}", kind) for i, kind in enumerate(kinds)]
    cfg = config(*backends, default=backends[default % len(backends)].name)
    measured = {b.name: seen(rates[i], 10 + i) for i, b in enumerate(backends)}
    first = order(cfg, measured)
    assert first == order(cfg, measured)  # deterministic
    assert len(first) == len(set(first))  # no backend twice
    chosen_default = cfg.backend()
    if chosen_default.kind == "local":
        assert all(cfg.backend(name).kind == "local" for name in first)
    assert chosen_default.name in first


# --- through the server ------------------------------------------------------------------------


def write_config(*, routing: str = "auto") -> None:
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        f'default_backend = "main"\nrouting = "{routing}"\n'
        '[[backends]]\nname = "main"\nbase_url = "http://main"\n'
        '[[backends]]\nname = "spare"\nbase_url = "http://spare"\n',
        encoding="utf-8",
    )


class Fleet:
    """Two scripted model servers: `up` says which answer, `chat` what each one replies."""

    def __init__(self, up: dict[str, bool], chat: dict[str, Any]) -> None:
        self.up = up
        self.chat = chat
        self.asked: list[str] = []

    def client(self, backend: Backend) -> ChatClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/models":
                return httpx.Response(200 if self.up[backend.name] else 503, json={"data": []})
            self.asked.append(backend.name)
            reply = self.chat[backend.name]
            if isinstance(reply, int):
                return httpx.Response(reply, json={})
            return httpx.Response(200, json=reply)

        return ChatClient(backend, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def run_delegate(
    tmp_path: Path, fleet: Fleet, monkeypatch: pytest.MonkeyPatch, **extra: Any
) -> dict[str, Any]:
    monkeypatch.setattr(server, "ChatClient", fleet.client)
    args = {"task": "go", "workdir": str(tmp_path), **extra}
    result = asyncio.run(build_server().call_tool("delegate", args))
    return result.structured_content  # type: ignore[union-attr]


def last_route() -> str:
    lines = (state_dir() / "runs.jsonl").read_text().splitlines()
    return json.loads(lines[-1])["route"]


def test_a_backend_that_does_not_answer_is_skipped_and_the_reason_is_traced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config()
    chat = {"main": message("main"), "spare": message("spare")}
    fleet = Fleet({"main": False, "spare": True}, chat)
    result = run_delegate(tmp_path, fleet, monkeypatch)
    assert (result["status"], result["answer"]) == ("ok", "spare")
    assert fleet.asked == ["spare"]
    assert last_route() == "main not answering; chose spare (local, not measured)"


def test_a_backend_error_moves_on_to_the_next_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config()
    fleet = Fleet({"main": True, "spare": True}, {"main": 500, "spare": message("spare")})
    result = run_delegate(tmp_path, fleet, monkeypatch)
    assert (result["status"], result["answer"]) == ("ok", "spare")
    assert fleet.asked == ["main", "spare"]
    assert "main backend error; chose spare" in last_route()


def test_the_measured_faster_backend_is_chosen_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config()
    for name, elapsed in (("main", 40.0), ("spare", 8.0)):
        summary = Summary(
            variant="baseline", runs=1, pass_rate=1.0, citation_rate=1.0, mean_turns=2.0,
            mean_tokens_out=50, mean_elapsed_s=elapsed, stdev_elapsed_s=0, tokens_per_s=10,
            served_model="m",
        )  # fmt: skip
        save_model_profile(Backend(name=name, base_url=f"http://{name}"), summary, [])
    chat = {"main": message("main"), "spare": message("spare")}
    fleet = Fleet({"main": True, "spare": True}, chat)
    assert run_delegate(tmp_path, fleet, monkeypatch)["answer"] == "spare"
    assert last_route().startswith("chose spare (local, 100% passed, 8s)")


def test_when_nothing_answers_the_caller_is_told_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config()
    fleet = Fleet({"main": False, "spare": False}, {})
    result = run_delegate(tmp_path, fleet, monkeypatch)
    assert result["status"] == "error"
    assert result["answer"] == "no backend answers: main not answering; spare not answering"
    assert fleet.asked == []


def test_a_named_backend_or_routing_off_means_no_choice_and_no_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config()
    fleet = Fleet({"main": False, "spare": True}, {"main": 500, "spare": message("spare")})
    named = run_delegate(tmp_path, fleet, monkeypatch, backend="spare")
    assert named["answer"] == "spare"
    assert last_route() == "chose spare (named by the caller)"
    write_config(routing="off")
    both_up = {"main": True, "spare": True}
    off_fleet = Fleet(both_up, {"main": 500, "spare": message("x")})
    off = run_delegate(tmp_path, off_fleet, monkeypatch)
    assert off["status"] == "error"  # main failed and there is no fallback
    assert last_route() == "chose main (routing is off)"
