# pyright: basic
from collections.abc import Iterator
from pathlib import Path

import pytest
from fake_model import FakeModel, serve


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Everything under tests/integration may use the network (127.0.0.1) and is marked as such."""
    for item in items:
        if "tests/integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
            item.add_marker(pytest.mark.enable_socket)


@pytest.fixture
def model() -> Iterator[FakeModel]:
    with serve(FakeModel()) as running:
        yield running


@pytest.fixture
def configured(model: FakeModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    """A deputy configuration that points at the fake model."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'default_backend = "fake"\nauto_update = "off"\n'
        f'[[backends]]\nname = "fake"\nbase_url = "{model.base_url}"\nmodel = "fake-model"\n'
    )
    monkeypatch.setenv("DEPUTY_HOME", str(home))
    return model
