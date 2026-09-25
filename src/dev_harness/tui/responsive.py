"""Terminal degradation below 80x24 (V11 task 9.5).

The four-panel grid needs at least 80 columns x 24 rows. Below that the
shell degrades to a single-panel fallback: only the execution canvas stays
visible, so the operator still sees the live run instead of a broken grid.

The decision is a pure function of the terminal size so it can be unit
tested without a running app; :class:`~dev_harness.tui.app.HermesApp` wires
it into its resize handler.
"""

from __future__ import annotations

from typing import Final

from dev_harness.contracts.enums import PanelId

#: Minimum terminal size that fits the full four-panel grid (V11 9.5).
MIN_WIDTH: Final = 80
MIN_HEIGHT: Final = 24

#: The single panel kept visible in the degraded layout.
FALLBACK_PANEL: Final = PanelId.CANVAS


def is_degraded(width: int, height: int) -> bool:
    """Return ``True`` when ``width`` x ``height`` is too small for four panels."""
    return width < MIN_WIDTH or height < MIN_HEIGHT


def visible_panels(width: int, height: int) -> frozenset[PanelId]:
    """Return the panels visible at ``width`` x ``height``.

    Below 80x24 only :data:`FALLBACK_PANEL` (the execution canvas) is shown;
    at or above it all four panels are shown.
    """
    if is_degraded(width, height):
        return frozenset({FALLBACK_PANEL})
    return frozenset(PanelId)
