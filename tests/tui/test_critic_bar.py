"""Critic-bar panel tests (V11 task 7.6).

Unit tests drive ``emit`` directly and assert the exact envelope shape; the
integration test mounts the panel under a real ``HermesApp`` and clicks
``#btn-pause`` via the pilot, asserting **exactly one**
``INTERRUPT_REQUEST{PAUSE}`` is emitted. No ``time.sleep`` — the click settles
via ``pilot.pause``.
"""

from __future__ import annotations

import pytest
from textual.widgets import Button, Input

from dev_harness.contracts.enums import CriticCommand, EventType
from dev_harness.contracts.events import Envelope, InterruptRequestPayload
from dev_harness.tui.app import CRITIC_BAR_ID, HermesApp
from dev_harness.tui.panels.critic_bar import (
    BTN_APPROVE_ID,
    BTN_PAUSE_ID,
    BTN_REJECT_ID,
    BTN_RESUME_ID,
    BTN_STOP_ID,
    BUTTON_COMMANDS,
    CRITIC_INPUT_ID,
    CriticBar,
)

SIZE = (100, 30)


def interrupt_requests(panel: CriticBar) -> list[Envelope]:
    """The emitted envelopes that are ``INTERRUPT_REQUEST``."""
    return [env for env in panel.emitted if env.type is EventType.INTERRUPT_REQUEST]


# --- unit -------------------------------------------------------------------


@pytest.mark.unit
def test_emit_pause_produces_exactly_one_envelope() -> None:
    """``emit(PAUSE)`` yields one ``INTERRUPT_REQUEST`` with ``command == PAUSE``."""
    panel = CriticBar()
    envelope = panel.emit(CriticCommand.PAUSE)
    assert envelope.type is EventType.INTERRUPT_REQUEST
    assert isinstance(envelope.payload, InterruptRequestPayload)
    assert envelope.payload.command is CriticCommand.PAUSE
    assert len(panel.emitted) == 1
    assert panel.last_command is CriticCommand.PAUSE


@pytest.mark.unit
def test_emit_reason_defaults_to_empty() -> None:
    """``reason`` defaults to ``""`` when omitted."""
    panel = CriticBar()
    envelope = panel.emit(CriticCommand.STOP)
    assert isinstance(envelope.payload, InterruptRequestPayload)
    assert envelope.payload.reason == ""


@pytest.mark.unit
def test_emit_reason_is_passed_through() -> None:
    """An explicit ``reason`` is carried on the payload."""
    panel = CriticBar()
    envelope = panel.emit(CriticCommand.RESUME, "operator note")
    assert isinstance(envelope.payload, InterruptRequestPayload)
    assert envelope.payload.reason == "operator note"


@pytest.mark.unit
def test_publish_sink_receives_exactly_one_call() -> None:
    """The injected sink is called once per emission, with the same envelope."""
    received: list[Envelope] = []
    panel = CriticBar(publish=received.append)
    envelope = panel.emit(CriticCommand.PAUSE)
    assert received == [envelope]
    assert len(received) == 1


@pytest.mark.unit
def test_no_sink_buffers_in_emitted() -> None:
    """With no sink the envelope is still buffered for assertions."""
    panel = CriticBar()
    panel.emit(CriticCommand.PAUSE)
    assert len(panel.emitted) == 1


@pytest.mark.unit
def test_last_command_none_before_any_emission() -> None:
    """``last_command`` is ``None`` until the first emission."""
    assert CriticBar().last_command is None


# --- integration ------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_click_pause_emits_exactly_one_interrupt_request() -> None:
    """Clicking ``#btn-pause`` emits exactly one ``INTERRUPT_REQUEST{PAUSE}``."""
    app = HermesApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        panel = app.query_one(CRITIC_BAR_ID, CriticBar)
        assert app.query_one(CRITIC_INPUT_ID, Input) is not None
        assert app.query_one(BTN_PAUSE_ID, Button) is not None

        await pilot.click(BTN_PAUSE_ID)
        await pilot.pause()

        requests = interrupt_requests(panel)
        assert len(requests) == 1
        payload = requests[0].payload
        assert isinstance(payload, InterruptRequestPayload)
        assert payload.command is CriticCommand.PAUSE


@pytest.mark.integration
@pytest.mark.asyncio
async def test_input_value_becomes_reason() -> None:
    """A non-empty Input value is carried as the emitted ``reason``."""
    app = HermesApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        panel = app.query_one(CRITIC_BAR_ID, CriticBar)
        app.query_one(CRITIC_INPUT_ID, Input).value = "hold on"

        await pilot.click(BTN_PAUSE_ID)
        await pilot.pause()

        requests = interrupt_requests(panel)
        assert len(requests) == 1
        payload = requests[0].payload
        assert isinstance(payload, InterruptRequestPayload)
        assert payload.reason == "hold on"


# --- negative ---------------------------------------------------------------


@pytest.mark.negative
@pytest.mark.asyncio
async def test_click_resume_emits_resume_not_pause() -> None:
    """Clicking ``#btn-resume`` emits RESUME, never PAUSE."""
    app = HermesApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        panel = app.query_one(CRITIC_BAR_ID, CriticBar)

        await pilot.click(BTN_RESUME_ID)
        await pilot.pause()

        requests = interrupt_requests(panel)
        assert len(requests) == 1
        payload = requests[0].payload
        assert isinstance(payload, InterruptRequestPayload)
        assert payload.command is CriticCommand.RESUME
        assert payload.command is not CriticCommand.PAUSE


@pytest.mark.negative
@pytest.mark.asyncio
async def test_two_clicks_emit_two_distinct_envelopes() -> None:
    """Two clicks emit two envelopes — no dedup at this layer (documented)."""
    app = HermesApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        panel = app.query_one(CRITIC_BAR_ID, CriticBar)

        await pilot.click(BTN_PAUSE_ID)
        await pilot.pause()
        await pilot.click(BTN_RESUME_ID)
        await pilot.pause()

        requests = interrupt_requests(panel)
        assert len(requests) == 2
        assert requests[0] is not requests[1]
        first = requests[0].payload
        second = requests[1].payload
        assert isinstance(first, InterruptRequestPayload)
        assert isinstance(second, InterruptRequestPayload)
        assert first.command is CriticCommand.PAUSE
        assert second.command is CriticCommand.RESUME


@pytest.mark.negative
def test_unknown_button_id_does_not_raise() -> None:
    """A ``Button.Pressed`` for an unmapped id is ignored, not an error."""
    panel = CriticBar()
    event = Button.Pressed(Button("mystery", id="btn-mystery"))
    panel.on_button_pressed(event)
    assert panel.emitted == []
    assert panel.last_command is None


@pytest.mark.negative
def test_hitl_buttons_map_to_real_commands() -> None:
    """Approve/Reject map to real ``CriticCommand`` members (RESUME/STOP)."""
    assert BUTTON_COMMANDS[BTN_APPROVE_ID] is CriticCommand.RESUME
    assert BUTTON_COMMANDS[BTN_REJECT_ID] is CriticCommand.STOP
    assert BUTTON_COMMANDS[BTN_PAUSE_ID] is CriticCommand.PAUSE
    assert BUTTON_COMMANDS[BTN_RESUME_ID] is CriticCommand.RESUME
    assert BUTTON_COMMANDS[BTN_STOP_ID] is CriticCommand.STOP