"""HermesApp shell layout tests (V11 task 7.1).

Headless mount via ``run_test``: the four region IDs must resolve and the
grid must render them visible and sized per ``app.tcss``.
"""

from __future__ import annotations

import pytest
from textual.geometry import Offset
from textual.widget import Widget

from dev_harness.tui.app import (
    CRITIC_BAR_ID,
    EXECUTION_CANVAS_ID,
    MODEL_REGISTRY_ID,
    REPO_MANAGER_ID,
    HermesApp,
)

pytestmark = pytest.mark.unit

#: 100x30 render (V11 7.1 acceptance) — grid is 3 columns x 2 rows.
SIZE = (100, 30)


@pytest.mark.asyncio
async def test_four_panel_ids_resolve() -> None:
    """All four region IDs resolve under run_test()."""
    app = HermesApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        for selector in (
            REPO_MANAGER_ID,
            EXECUTION_CANVAS_ID,
            MODEL_REGISTRY_ID,
            CRITIC_BAR_ID,
        ):
            widget = app.query_one(selector)
            assert isinstance(widget, Widget)
            assert widget.id is not None
            assert widget.visible


@pytest.mark.asyncio
async def test_grid_renders_four_regions() -> None:
    """The grid renders four visible regions sized per the CSS."""
    app = HermesApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        regions = {
            REPO_MANAGER_ID: app.query_one(REPO_MANAGER_ID),
            EXECUTION_CANVAS_ID: app.query_one(EXECUTION_CANVAS_ID),
            MODEL_REGISTRY_ID: app.query_one(MODEL_REGISTRY_ID),
            CRITIC_BAR_ID: app.query_one(CRITIC_BAR_ID),
        }
        for widget in regions.values():
            assert widget.visible
            assert widget.size.width > 0
            assert widget.size.height > 0

        # Center canvas is the largest region (3fr vs 1fr side columns).
        canvas = regions[EXECUTION_CANVAS_ID]
        for side in (REPO_MANAGER_ID, MODEL_REGISTRY_ID):
            assert canvas.size.width > regions[side].size.width

        # Critic bar spans the full width along the bottom.
        critic = regions[CRITIC_BAR_ID]
        assert critic.region.width == app.size.width
        assert critic.region.offset.y >= canvas.region.offset.y


@pytest.mark.asyncio
async def test_grid_geometry_matches_css() -> None:
    """Region geometry matches the 3x2 grid: side columns equal, canvas widest."""
    app = HermesApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        left = app.query_one(REPO_MANAGER_ID)
        right = app.query_one(MODEL_REGISTRY_ID)
        canvas = app.query_one(EXECUTION_CANVAS_ID)
        critic = app.query_one(CRITIC_BAR_ID)

        assert left.size.width == right.size.width
        assert canvas.size.width > left.size.width
        assert critic.region.width == app.size.width
        # Side regions sit above the critic bar (row 1 of 2).
        assert left.region.offset.y < critic.region.offset.y
        assert right.region.offset.y < critic.region.offset.y
        # Canvas occupies the center column.
        assert canvas.region.offset.x >= left.region.offset.x + left.size.width
        assert canvas.region.offset.x + canvas.size.width <= right.region.offset.x
        # Regions are laid out left-to-right, top-to-bottom.
        assert left.region.offset.x < canvas.region.offset.x < right.region.offset.x
        assert left.region.offset == Offset(0, 0)