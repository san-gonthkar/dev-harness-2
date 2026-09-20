"""Marker enforcement helper (V11 0.18).

Every test must declare exactly one marker. This pytest plugin fails collection
for unmarked or multi-marked tests. Module-level ``pytestmark`` counts toward
each item via ``iter_markers``.
"""

from __future__ import annotations

import pytest

ALLOWED_MARKERS = {"unit", "property", "contract", "integration", "negative", "timing", "slow", "e2e"}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        func_markers = {
            m.name for m in item.iter_markers() if m.name in ALLOWED_MARKERS
        }
        if len(func_markers) != 1:
            raise pytest.UsageError(
                f"{item.nodeid}: must declare exactly one marker "
                f"({sorted(func_markers)} found)"
            )
