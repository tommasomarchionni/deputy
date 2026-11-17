# pyright: basic
"""Tests against a real model server. Skipped unless DEPUTY_LIVE_URL is set, so CI never runs them.

DEPUTY_LIVE_URL=http://127.0.0.1:8080 DEPUTY_LIVE_KEY_FILE=~/key uv run pytest tests/live
"""

import asyncio
import os

import pytest

from deputy.bench import Variant, run_suite
from deputy.config import Backend

pytestmark = pytest.mark.live

LIVE_URL = os.environ.get("DEPUTY_LIVE_URL")


@pytest.mark.skipif(not LIVE_URL, reason="set DEPUTY_LIVE_URL to run against a real model")
def test_the_real_model_passes_the_quick_benchmark_tasks() -> None:
    backend = Backend(
        name="live",
        base_url=LIVE_URL or "",
        model=os.environ.get("DEPUTY_LIVE_MODEL", "default"),
        api_key_file=os.environ.get("DEPUTY_LIVE_KEY_FILE"),
        timeout_s=900,
    )
    quick = ["cited-extract", "needle-in-long-file", "refuse-secret"]
    summary = asyncio.run(run_suite(backend, Variant(name="live"), runs=1, only=quick))
    assert summary.pass_rate >= 0.66, summary.failures
    assert summary.citation_rate == 1.0
