"""CriticGatekeeper state tests (V11 6.1).

Validation matrix: all 16 (state, command) pairs match the normative 0.22
table; RESUME from STOPPED raises IllegalTransitionError; no undefined result
state is ever produced.
"""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import CriticCommand, ExecutionState
from dev_harness.contracts.errors import IllegalTransitionError
from dev_harness.core.critic import CriticGatekeeper

pytestmark = pytest.mark.unit

STATES = list(ExecutionState)
COMMANDS = list(CriticCommand)


def _gatekeeper(state: ExecutionState) -> CriticGatekeeper:
    return CriticGatekeeper(initial=state)


# --- READY row (0.22) -------------------------------------------------------


@pytest.mark.unit
def test_ready_start_running() -> None:
    gk = _gatekeeper(ExecutionState.READY)
    tr = gk.transition(CriticCommand.START)
    assert tr.result == ExecutionState.RUNNING
    assert tr.already is False
    assert gk.state == ExecutionState.RUNNING


@pytest.mark.unit
def test_ready_pause_noop_already_false() -> None:
    gk = _gatekeeper(ExecutionState.READY)
    tr = gk.transition(CriticCommand.PAUSE)
    assert tr.result == ExecutionState.READY
    assert tr.already is False
    assert gk.state == ExecutionState.READY


@pytest.mark.unit
def test_ready_resume_illegal() -> None:
    gk = _gatekeeper(ExecutionState.READY)
    with pytest.raises(IllegalTransitionError) as exc:
        gk.transition(CriticCommand.RESUME)
    assert exc.value.state == ExecutionState.READY
    assert exc.value.command == CriticCommand.RESUME
    assert gk.state == ExecutionState.READY


@pytest.mark.unit
def test_ready_stop_stopped() -> None:
    gk = _gatekeeper(ExecutionState.READY)
    tr = gk.transition(CriticCommand.STOP)
    assert tr.result == ExecutionState.STOPPED
    assert tr.already is False
    assert gk.state == ExecutionState.STOPPED


# --- RUNNING row (0.22) -----------------------------------------------------


@pytest.mark.unit
def test_running_start_illegal() -> None:
    gk = _gatekeeper(ExecutionState.RUNNING)
    with pytest.raises(IllegalTransitionError):
        gk.transition(CriticCommand.START)
    assert gk.state == ExecutionState.RUNNING


@pytest.mark.unit
def test_running_pause_paused() -> None:
    gk = _gatekeeper(ExecutionState.RUNNING)
    tr = gk.transition(CriticCommand.PAUSE)
    assert tr.result == ExecutionState.PAUSED
    assert tr.already is False
    assert gk.state == ExecutionState.PAUSED


@pytest.mark.unit
def test_running_resume_illegal() -> None:
    gk = _gatekeeper(ExecutionState.RUNNING)
    with pytest.raises(IllegalTransitionError):
        gk.transition(CriticCommand.RESUME)
    assert gk.state == ExecutionState.RUNNING


@pytest.mark.unit
def test_running_stop_stopped() -> None:
    gk = _gatekeeper(ExecutionState.RUNNING)
    tr = gk.transition(CriticCommand.STOP)
    assert tr.result == ExecutionState.STOPPED
    assert tr.already is False
    assert gk.state == ExecutionState.STOPPED


# --- PAUSED row (0.22) ------------------------------------------------------


@pytest.mark.unit
def test_paused_start_illegal() -> None:
    gk = _gatekeeper(ExecutionState.PAUSED)
    with pytest.raises(IllegalTransitionError):
        gk.transition(CriticCommand.START)
    assert gk.state == ExecutionState.PAUSED


@pytest.mark.unit
def test_paused_pause_idempotent_already_true() -> None:
    gk = _gatekeeper(ExecutionState.PAUSED)
    tr = gk.transition(CriticCommand.PAUSE)
    assert tr.result == ExecutionState.PAUSED
    assert tr.already is True
    assert gk.state == ExecutionState.PAUSED


@pytest.mark.unit
def test_paused_resume_running() -> None:
    gk = _gatekeeper(ExecutionState.PAUSED)
    tr = gk.transition(CriticCommand.RESUME)
    assert tr.result == ExecutionState.RUNNING
    assert tr.already is False
    assert gk.state == ExecutionState.RUNNING


@pytest.mark.unit
def test_paused_stop_stopped() -> None:
    gk = _gatekeeper(ExecutionState.PAUSED)
    tr = gk.transition(CriticCommand.STOP)
    assert tr.result == ExecutionState.STOPPED
    assert tr.already is False
    assert gk.state == ExecutionState.STOPPED


# --- STOPPED row (0.22) -----------------------------------------------------


@pytest.mark.unit
def test_stopped_start_illegal() -> None:
    gk = _gatekeeper(ExecutionState.STOPPED)
    with pytest.raises(IllegalTransitionError):
        gk.transition(CriticCommand.START)
    assert gk.state == ExecutionState.STOPPED


@pytest.mark.unit
def test_stopped_pause_illegal() -> None:
    gk = _gatekeeper(ExecutionState.STOPPED)
    with pytest.raises(IllegalTransitionError):
        gk.transition(CriticCommand.PAUSE)
    assert gk.state == ExecutionState.STOPPED


@pytest.mark.unit
def test_stopped_resume_illegal() -> None:
    """RESUME from STOPPED is the named illegal pair in 6.B."""
    gk = _gatekeeper(ExecutionState.STOPPED)
    with pytest.raises(IllegalTransitionError) as exc:
        gk.transition(CriticCommand.RESUME)
    assert exc.value.state == ExecutionState.STOPPED
    assert exc.value.command == CriticCommand.RESUME
    assert gk.state == ExecutionState.STOPPED


@pytest.mark.unit
def test_stopped_stop_idempotent_already_true() -> None:
    gk = _gatekeeper(ExecutionState.STOPPED)
    tr = gk.transition(CriticCommand.STOP)
    assert tr.result == ExecutionState.STOPPED
    assert tr.already is True
    assert gk.state == ExecutionState.STOPPED


# --- exhaustive sweep: no undefined result state ----------------------------


@pytest.mark.unit
def test_all_16_cells_match_0_22_table() -> None:
    """Every (state, command) pair resolves to a defined result or raises."""
    expected: dict[ExecutionState, dict[CriticCommand, ExecutionState | None]] = {
        ExecutionState.READY: {
            CriticCommand.START: ExecutionState.RUNNING,
            CriticCommand.PAUSE: ExecutionState.READY,
            CriticCommand.RESUME: None,  # ILLEGAL
            CriticCommand.STOP: ExecutionState.STOPPED,
        },
        ExecutionState.RUNNING: {
            CriticCommand.START: None,
            CriticCommand.PAUSE: ExecutionState.PAUSED,
            CriticCommand.RESUME: None,
            CriticCommand.STOP: ExecutionState.STOPPED,
        },
        ExecutionState.PAUSED: {
            CriticCommand.START: None,
            CriticCommand.PAUSE: ExecutionState.PAUSED,
            CriticCommand.RESUME: ExecutionState.RUNNING,
            CriticCommand.STOP: ExecutionState.STOPPED,
        },
        ExecutionState.STOPPED: {
            CriticCommand.START: None,
            CriticCommand.PAUSE: None,
            CriticCommand.RESUME: None,
            CriticCommand.STOP: ExecutionState.STOPPED,
        },
    }
    for state in STATES:
        for command in COMMANDS:
            gk = _gatekeeper(state)
            want = expected[state][command]
            if want is None:
                with pytest.raises(IllegalTransitionError):
                    gk.transition(command)
                assert gk.state == state
            else:
                tr = gk.transition(command)
                assert tr.result == want
                assert gk.state == want


@pytest.mark.unit
def test_no_undefined_result_state() -> None:
    """No transition may ever leave the gatekeeper in an undefined state."""
    for state in STATES:
        for command in COMMANDS:
            gk = _gatekeeper(state)
            try:
                tr = gk.transition(command)
            except IllegalTransitionError:
                continue
            assert tr.result in STATES
            assert gk.state in STATES


@pytest.mark.unit
def test_default_initial_state_is_ready() -> None:
    """A fresh gatekeeper starts in READY."""
    assert CriticGatekeeper().state == ExecutionState.READY
