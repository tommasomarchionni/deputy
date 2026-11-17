"""The installed version of deputy and how versions compare."""

from __future__ import annotations

import re
from importlib import metadata


def current_version() -> str:
    try:
        return metadata.version("deputy")
    except metadata.PackageNotFoundError:
        return "0+unknown"


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' and '1.2.3rc1' become (1, 2, 3). Anything unparseable becomes (0,)."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else (0,)


def is_newer(candidate: str, installed: str) -> bool:
    return parse_version(candidate) > parse_version(installed)


def is_compatible(a: str, b: str) -> bool:
    """Whether two versions may be swapped without asking: same major, and for 0.x (where any
    minor may break things) the same minor too."""
    left, right = parse_version(a), parse_version(b)
    depth = 2 if left[0] == 0 or right[0] == 0 else 1
    return left[:depth] == right[:depth]
