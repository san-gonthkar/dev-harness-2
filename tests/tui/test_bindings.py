"""Keybinding tests (V11 task 7.9).

Unit tests assert the binding table shape and the confirm modal's dismiss
values; integration tests drive the real ``HermesApp`` under ``run_test`` and
press ``ctrl+c`` / ``ctrl+q`` via the pilot. No ``time.sleep`` — the pilot's
``pause`` settles each keypress.
"""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import CriticCommand, EventType
from dev_harness.contracts.events import InterruptRequestPayload
from dev_harness.tui.app import HermesApp
from dev_harness.tui.bindings import (
    CONFIRM_NO_ID,
    CONFIRM_YES_ID,
    HERMES_BINDINGS,
    ConfirmQuitScreen,
)

SIZE = (100, 30)


# --- unit -------------------------------------------------------------------


@pytest.mark.unit
def test_bindings_contain_ctrl_c_priority_and_ctrl_q() -> None:
    """``ctrl+c`` is priority-bound to pause; ``ctrl+q`` requests quit."""
    by_key = {binding.key: binding for binding in HermesApp.BINDINGS}
    assert "ctrl+c" in by_key
    assert by_key["ctrl+c"].priority is True
    assert by_key["ctrl+c"].action == "pause"
    assert "ctrl+q" in by_key
    assert by_key["ctrl+q"].action == "request_quit"
    assert HERMES_BINDINGS == list(HermesApp.BINDINGS)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_confirm_screen_dismisses_true_on_yes() -> None:
    """Clicking ``#confirm-yes`` dismisses the modal with ``True``."""
    app = HermesApp()
    results: list[bool | None] = []
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        app.push_screen(ConfirmQuitScreen(), results.append)
        await pilot.pause()
        await pilot.click(f"#{CONFIRM_YES_ID}")
        await pilot.pause()
    assert results == [True]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_confirm_screen_dismisses_false_on_no() -> None:
    """Clicking ``#confirm-no`` dismisses the modal with ``False``."""
    app = HermesApp()
    results: list[bool | None] = []
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        app.push_screen(ConfirmQuitScreen(), results.append)
        await pilot.pause()
        await pilot.click(f"#{CONFIRM_NO_ID}")
        await pilot.pause()
    assert results == [False]


# --- integration ------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ctrl_c_pauses_and_keeps_running() -> None:
    """``ctrl+c`` emits exactly one ``INTERRUPT_REQUEST{PAUSE}`` and does not exit."""
    app = HermesApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()

        assert app.is_running
        assert len(app.pause_requests) == 1
        envelope = app.pause_requests[0]
        assert envelope.type is EventType.INTERRUPT_REQUEST
        payload = envelope.payload
        assert isinstance(payload, InterruptRequestPayload)
        assert payload.command is CriticCommand.PAUSE


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ctrl_q_opens_confirm_modal_and_no_keeps_running() -> None:
    """``ctrl+q`` opens the modal; dismissing with No leaves the app running."""
    app = HermesApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmQuitScreen)

        await pilot.click(f"#{CONFIRM_NO_ID}")
        await pilot.pause()
        assert app.is_running
        assert not isinstance(app.screen, ConfirmQuitScreen)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_yes_exits_the_app() -> None:
    """Confirming Yes on the modal exits the app cleanly."""
    app = HermesApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        await pilot.click(f"#{CONFIRM_YES_ID}")
        await pilot.pause()
        assert not app.is_running


# --- negative ---------------------------------------------------------------


@pytest.mark.negative
@pytest.mark.asyncio
async def test_ctrl_c_does_not_exit_the_app() -> None:
    """The core rejection criterion: ``ctrl+c`` must never quit the app."""
    app = HermesApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app.is_running
        assert not isinstance(app.screen, ConfirmQuitScreen)


@pytest.mark.negative
@pytest.mark.asyncio
async def test_modal_no_does_not_exit_the_app() -> None:
    """Dismissing the confirm modal with No does not exit."""
    app = HermesApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        await pilot.click(f"#{CONFIRM_NO_ID}")
        await pilot.pause()
        assert app.is_running
