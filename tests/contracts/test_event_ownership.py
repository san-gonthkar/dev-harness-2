"""Event ownership tests (V11 0.20)."""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import EventType
from scripts.check_event_ownership import OWNERSHIP, check

pytestmark = pytest.mark.unit


def test_all_event_types_have_producer_and_consumer() -> None:
    assert check() == []


def test_nine_members() -> None:
    assert len(EventType) == 9
    assert len(OWNERSHIP) == 9


def test_snapshot_exists() -> None:
    assert "SNAPSHOT" in OWNERSHIP
    assert OWNERSHIP["SNAPSHOT"][0]  # has a producer


def test_matrix_covers_all_members() -> None:
    members = {e.value for e in EventType}
    assert set(OWNERSHIP) == members
