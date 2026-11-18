"""Token estimates for what the orchestrator reads and pays for.

The estimator is deliberately simple and says so: the size of the text in UTF-8 bytes divided by
four, rounded up. That is the usual rule of thumb for English prose with the tokenizers of current
models. It is wrong by up to about a quarter either way (code and JSON are denser, so they cost
more than this says; text in other languages varies), but it is the same yardstick for both sides
of every comparison, so the *difference* it gives (what delegating saved) is meaningful as an
order of magnitude and a trend, not as an invoice.
"""

from __future__ import annotations

import json
from typing import Any

BYTES_PER_TOKEN = 4


def estimate(text: str) -> int:
    """Estimated tokens of a text (bytes / 4, rounded up; an empty text is zero)."""
    return -(-len(text.encode("utf-8")) // BYTES_PER_TOKEN)


def estimate_value(value: Any) -> int:
    """Estimated tokens of a value as the orchestrator sees it: serialised as JSON."""
    return estimate(json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")))


def from_chars(chars: int) -> int:
    """Estimated tokens of text that is known only by its length in characters."""
    return -(-chars // BYTES_PER_TOKEN)
