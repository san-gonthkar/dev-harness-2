"""Critic command handler tests (V11 6.2).

Validation matrix: two PAUSEs produce one transition, two ACKs, and the
second ACK carries ``already=True``. Also covers the other idempotent cell
(STOP while STOPPED), illegal commands (no ACK, state unchanged), and the
emit sink wiring.
"""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import CriticCommand, EventType, ExecutionState
from dev_harness.contracts.errors import IllegalTransitionError
from dev_harness.core.critic import CriticGatekeeper
from dev_harness.core.critic_commands import CriticCommandHandler

pytestmark = pytest.mark.unit


def _handler(
    initial: ExecutionState = ExecutionState.READY,
) -> tuple[CriticCommandHandler, list]:
    """A handler with a recording emit sink; returns (handler, sink)."""
    emitted: list = []
    handler = CriticCommandHandler(
        gatekeeper=CriticGatekeeper(initial=initial), emit=emitted.append
    )
    return handler, emitted


# --- 6.B row: two PAUSEs -> one transition, two ACKs, second already=True ---


@pytest.mark.unit
def test_two_pauses_one_transition_two_acks_second_already() -> None:
    handler, emitted = _handler(initial=ExecutionState.RUNNING)

    first = handler.handle(CriticCommand.PAUSE)
    second = handler.handle(CriticCommand.PAUSE)

    # One transition: RUNNING -> PAUSED, then PAUSED stays PAUSED.
    assert handler.state == ExecutionState.PAUSED
    # Two ACKs, both INTERRUPT_ACK, strictly increasing seq.
    assert len(emitted) == 2
    assert [e.type for e in emitted] == [EventType.INTERRUPT_ACK] * 2
    assert [e.seq for e in emitted] == [0, 1]
    # First ACK: real transition, already=False. Second: already=True.
    assert first.payload.command == CriticCommand.PAUSE
    assert first.payload.already is False
    assert second.payload.command == CriticCommand.PAUSE
    assert second.payload.already is True


# --- idempotent STOP while STOPPED (0.22 D2) --------------------------------


@pytest.mark.unit
def test_stop_while_stopped_ack_already_true() -> None:
    handler, emitted = _handler(initial=ExecutionState.STOPPED)

    ack = handler.handle(CriticCommand.STOP)

    assert handler.state == ExecutionState.STOPPED
    assert ack.payload.already is True
    assert len(emitted) == 1


# --- legal transitions emit exactly one ACK each ----------------------------


@pytest.mark.unit
def test_start_then_pause_then_resume_three_acks() -> None:
    handler, emitted = _handler(initial=ExecutionState.READY)

    handler.handle(CriticCommand.START)
    handler.handle(CriticCommand.PAUSE)
    handler.handle(CriticCommand.RESUME)

    assert handler.state == ExecutionState.RUNNING
    assert len(emitted) == 3
    assert [e.payload.already for e in emitted] == [False, False, False]


# --- illegal commands: no ACK, state unchanged ------------------------------


@pytest.mark.unit
def test_illegal_resume_from_ready_raises_and_emits_nothing() -> None:
    handler, emitted = _handler(initial=ExecutionState.READY)

    with pytest.raises(IllegalTransitionError) as exc:
        handler.handle(CriticCommand.RESUME)

    assert exc.value.state == ExecutionState.READY
    assert exc.value.command == CriticCommand.RESUME
    assert handler.state == ExecutionState.READY
    assert emitted == []


@pytest.mark.unit
def test_illegal_pause_from_stopped_raises_and_emits_nothing() -> None:
    handler, emitted = _handler(initial=ExecutionState.STOPPED)

    with pytest.raises(IllegalTransitionError):
        handler.handle(CriticCommand.PAUSE)

    assert handler.state == ExecutionState.STOPPED
    assert emitted == []


# --- emit sink wiring -------------------------------------------------------


@pytest.mark.unit
def test_default_handler_uses_fresh_gatekeeper() -> None:
    handler = CriticCommandHandler()
    assert handler.state == ExecutionState.READY


@pytest.mark.unit
def test_ack_envelope_type_matches_payload() -> None:
    handler, emitted = _handler(initial=ExecutionState.RUNNING)

    handler.handle(CriticCommand.PAUSE)

    envelope = emitted[0]
    # Envelope construction already enforces type/payload match; assert the
    # discriminated payload is the ACK payload with the right command.
    assert envelope.type == EventType.INTERRUPT_ACK
    assert envelope.payload.type == "INTERRUPT_ACK"
    assert envelope.payload.command == CriticCommand.PAUSE
