"""Critic transition table tests (V11 0.22)."""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import CriticCommand, ExecutionState
from dev_harness.contracts.errors import IllegalTransitionError
from dev_harness.contracts.transitions import apply_transition

pytestmark = pytest.mark.unit


def test_ready_start_running() -> None:
    tr = apply_transition(ExecutionState.READY, CriticCommand.START)
    assert tr.result == ExecutionState.RUNNING
    assert tr.already is False


def test_ready_pause_noop_already_false() -> None:
    tr = apply_transition(ExecutionState.READY, CriticCommand.PAUSE)
    assert tr.result == ExecutionState.READY
    assert tr.already is False


def test_ready_resume_illegal() -> None:
    with pytest.raises(IllegalTransitionError) as exc:
        apply_transition(ExecutionState.READY, CriticCommand.RESUME)
    assert exc.value.state == ExecutionState.READY
    assert exc.value.command == CriticCommand.RESUME


def test_ready_stop_stopped() -> None:
    assert (
        apply_transition(ExecutionState.READY, CriticCommand.STOP).result
        == ExecutionState.STOPPED
    )


def test_running_start_illegal() -> None:
    with pytest.raises(IllegalTransitionError):
        apply_transition(ExecutionState.RUNNING, CriticCommand.START)


def test_running_pause_paused() -> None:
    assert (
        apply_transition(ExecutionState.RUNNING, CriticCommand.PAUSE).result
        == ExecutionState.PAUSED
    )


def test_running_resume_illegal() -> None:
    with pytest.raises(IllegalTransitionError):
        apply_transition(ExecutionState.RUNNING, CriticCommand.RESUME)


def test_running_stop_stopped() -> None:
    assert (
        apply_transition(ExecutionState.RUNNING, CriticCommand.STOP).result
        == ExecutionState.STOPPED
    )


def test_paused_pause_idempotent_already_true() -> None:
    tr = apply_transition(ExecutionState.PAUSED, CriticCommand.PAUSE)
    assert tr.result == ExecutionState.PAUSED
    assert tr.already is True


def test_paused_resume_running() -> None:
    assert (
        apply_transition(ExecutionState.PAUSED, CriticCommand.RESUME).result
        == ExecutionState.RUNNING
    )


def test_paused_stop_stopped() -> None:
    assert (
        apply_transition(ExecutionState.PAUSED, CriticCommand.STOP).result
        == ExecutionState.STOPPED
    )


def test_paused_start_illegal() -> None:
    with pytest.raises(IllegalTransitionError):
        apply_transition(ExecutionState.PAUSED, CriticCommand.START)


def test_stopped_resume_illegal() -> None:
    with pytest.raises(IllegalTransitionError) as exc:
        apply_transition(ExecutionState.STOPPED, CriticCommand.RESUME)
    assert exc.value.state == ExecutionState.STOPPED
    assert exc.value.command == CriticCommand.RESUME


def test_stopped_stop_idempotent() -> None:
    tr = apply_transition(ExecutionState.STOPPED, CriticCommand.STOP)
    assert tr.result == ExecutionState.STOPPED
    assert tr.already is True


def test_stopped_start_pause_illegal() -> None:
    with pytest.raises(IllegalTransitionError):
        apply_transition(ExecutionState.STOPPED, CriticCommand.START)
    with pytest.raises(IllegalTransitionError):
        apply_transition(ExecutionState.STOPPED, CriticCommand.PAUSE)
