"""Coverage weight derivation tests (V11 0.21)."""

from __future__ import annotations

import pytest

from scripts.coverage_weights import (
    PUBLISHED_BRANCH,
    PUBLISHED_LINE,
    WEIGHTS,
    derive_gate,
)

pytestmark = pytest.mark.unit


def test_weights_sum_to_100() -> None:
    assert sum(w for _, w, _, _ in WEIGHTS) == 100.0


def test_derived_gate_matches_published() -> None:
    line, branch = derive_gate()
    assert line == PUBLISHED_LINE
    assert branch == PUBLISHED_BRANCH


def test_all_eleven_packages() -> None:
    assert len(WEIGHTS) == 11
