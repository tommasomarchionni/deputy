"""Small types shared by the command modules."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any, Protocol

Command = Callable[[argparse.Namespace], int]


class Subparsers(Protocol):
    """The part of argparse's sub-parser registry the command modules use."""

    def add_parser(self, name: str, **kwargs: Any) -> argparse.ArgumentParser: ...
