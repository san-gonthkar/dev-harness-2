"""Terminal degradation below 80x24 tests (V11 task 9.5).

Acceptance is exact: at 60x20 the app mounts with only ``#execution-canvas``
visible and no exception; at 100x30 all four panels stay visible.
"""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import PanelId
from dev_harness.tui.app import (
    CRITIC_BAR_ID,
    EXECUTION_CANVAS_ID,
    MODEL_REGISTRY_ID,
    REPO_MANAGER_ID,
    HermesApp,
)
from dev_harness.tui.responsive import (
    FALLBACK_PANEL,
    MIN_HEIGHT,
    MIN_WIDTH,
    is_degraded,
    visible_panels,
)

#: Degraded render (V11 9.5 acceptance) — below the 80x24 grid minimum.
SMALL_SIZE = (60, 20)
#: Full render (V11 7.1 acceptance) — fits the four-panel grid.
FULL_SIZE = (100, 30)


@pytest.mark.unit
def test_visible_panels_degrades_below_minimum() -> None:
    """Below 80x24 only the execution canvas is visible."""
    assert is_degraded(60, 20)
    assert visible_panels(60, 20) == frozenset({FALLBACK_PANEL})
    assert FALLBACK_PANEL is PanelId.CANVAS
    # Either dimension below the minimum degrades.
    assert visible_panels(MIN_WIDTH - 1, MIN_HEIGHT) == frozenset({FALLBACK_PANEL})
    assert visible_panels(MIN_WIDTH, MIN_HEIGHT - 1) == frozenset({FALLBACK_PANEL})


@pytest.mark.unit
def test_visible_panels_full_at_minimum() -> None:
    """At or above 80x24 all four panels are visible."""
    assert not is_degraded(MIN_WIDTH, MIN_HEIGHT)
    assert visible_panels(MIN_WIDTH, MIN_HEIGHT) == frozenset(PanelId)
    assert visible_panels(100, 30) == frozenset(PanelId)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_small_terminal_mounts_with_only_canvas_visible() -> None:
    """At 60x20 the app mounts and only #execution-canvas is visible."""
    app = HermesApp()
    async with app.run_test(size=SMALL_SIZE) as pilot:
        await pilot.pause()
        assert app.query_one(EXECUTION_CANVAS_ID).visible
        for selector in (REPO_MANAGER_ID, MODEL_REGISTRY_ID, CRITIC_BAR_ID):
            assert not app.query_one(selector).visible


@pytest.mark.asyncio
@pytest.mark.negative
async def test_full_terminal_keeps_all_panels_visible() -> None:
    """At 100x30 the degradation must not hide any panel."""
    app = HermesApp()
    async with app.run_test(size=FULL_SIZE) as pilot:
        await pilot.pause()
        for selector in (
            REPO_MANAGER_ID,
            EXECUTION_CANVAS_ID,
            MODEL_REGISTRY_ID,
            CRITIC_BAR_ID,
        ):
            assert app.query_one(selector).visible
