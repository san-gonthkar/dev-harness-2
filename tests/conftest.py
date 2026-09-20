"""Shared pytest fixtures (V11 0.12)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support.clock import FrozenClock, make_clock
from tests.support.workspace import make_workspace


@pytest.fixture
def frozen_clock() -> Iterator[FrozenClock]:
    """A frozen monotonic clock installed for the duration of the test."""
    clock = make_clock()
    clock.install()
    try:
        yield clock
    finally:
        clock.restore()


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """A git-backed workspace with an initial commit."""
    return make_workspace(tmp_path)
