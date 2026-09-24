"""Retry-ceiling router tests (V11 8.13).

Validation matrix (8.B, acceptance is exact):

(a) on the **4th inner-loop attempt** (ceiling 3) the router routes **BACK TO
    THE ARCHITECT**;
(b) after the **e2e ceiling (2)** it **escalates to HITL**;
(c) the run **TERMINATES with a FAILED terminal state** after the e2e ceiling -
    never an infinite loop.

Every branch of every ceiling is exercised (below / at / above, inner and e2e)
because ``engine/routing.py`` is in the 8.C high-coverage set (95/90). No
``time.sleep``, no network.
"""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import ExecutionState, RunOutcome
from dev_harness.engine.routing import (
    ARCHITECT_NODE,
    DEVELOPER_NODE,
    E2E_CEILING,
    HITL_NODE,
    INNER_LOOP_CEILING,
    FailureKind,
    route,
)


def _state(*, inner: int = 0, e2e: int = 0) -> dict[str, int]:
    """A minimal state carrying only the two retry counters."""
    return {"inner_loop_retry_count": inner, "e2e_retry_count": e2e}


@pytest.mark.unit
def test_ceilings_are_the_documented_values() -> None:
    """The ceilings are exactly the plan's values (3 inner, 2 e2e)."""
    assert INNER_LOOP_CEILING == 3
    assert E2E_CEILING == 2


# --- (a) inner loop: below ceiling retries, at/above routes to Architect -----


@pytest.mark.unit
@pytest.mark.parametrize("count", [0, 1, 2])
def test_inner_loop_below_ceiling_retries_developer(count: int) -> None:
    """Below the ceiling the inner loop retries the Developer."""
    decision = route(_state(inner=count), failure_kind=FailureKind.INNER_LOOP)
    assert decision.next_node == DEVELOPER_NODE
    assert decision.terminal is None
    assert decision.outcome is None
    assert decision.escalate_to_hitl is False


@pytest.mark.unit
def test_fourth_inner_loop_attempt_routes_to_architect() -> None:
    """(a) The 4th attempt (3 retries done) routes BACK TO THE ARCHITECT."""
    decision = route(
        _state(inner=INNER_LOOP_CEILING), failure_kind=FailureKind.INNER_LOOP
    )
    assert decision.next_node == ARCHITECT_NODE
    assert decision.terminal is None


@pytest.mark.unit
@pytest.mark.parametrize("count", [INNER_LOOP_CEILING + 1, 99])
def test_inner_loop_above_ceiling_still_routes_to_architect(count: int) -> None:
    """Above the ceiling the router keeps routing to the Architect (no loop)."""
    decision = route(_state(inner=count), failure_kind=FailureKind.INNER_LOOP)
    assert decision.next_node == ARCHITECT_NODE


# --- (b) e2e loop: below ceiling retries, at/above escalates to HITL ---------


@pytest.mark.unit
@pytest.mark.parametrize("count", [0, 1])
def test_e2e_below_ceiling_retries_developer(count: int) -> None:
    """Below the e2e ceiling the router retries the Developer."""
    decision = route(_state(e2e=count), failure_kind=FailureKind.E2E)
    assert decision.next_node == DEVELOPER_NODE
    assert decision.terminal is None
    assert decision.escalate_to_hitl is False


@pytest.mark.unit
@pytest.mark.parametrize("count", [E2E_CEILING, E2E_CEILING + 1, 99])
def test_e2e_at_or_above_ceiling_escalates_to_hitl(count: int) -> None:
    """(b) After the e2e ceiling the router escalates to HITL."""
    decision = route(_state(e2e=count), failure_kind=FailureKind.E2E)
    assert decision.next_node == HITL_NODE
    assert decision.escalate_to_hitl is True


# --- (c) e2e ceiling terminates FAILED, never an infinite loop ---------------


@pytest.mark.unit
@pytest.mark.parametrize("count", [E2E_CEILING, E2E_CEILING + 1, 99])
def test_e2e_at_or_above_ceiling_terminates_failed(count: int) -> None:
    """(c) The run terminates in STOPPED with outcome FAILED."""
    decision = route(_state(e2e=count), failure_kind=FailureKind.E2E)
    assert decision.terminal is ExecutionState.STOPPED
    assert decision.outcome is RunOutcome.FAILED


@pytest.mark.unit
def test_router_terminates_never_loops() -> None:
    """(c) Driving the router with increasing counters terminates FAILED."""
    state = _state()
    for _ in range(100):
        inner = route(state, failure_kind=FailureKind.INNER_LOOP)
        if inner.next_node == ARCHITECT_NODE:
            e2e = route(state, failure_kind=FailureKind.E2E)
            if e2e.terminal is not None:
                assert e2e.terminal is ExecutionState.STOPPED
                assert e2e.outcome is RunOutcome.FAILED
                return
            state["e2e_retry_count"] += 1
        else:
            state["inner_loop_retry_count"] += 1
    pytest.fail("router did not terminate within 100 steps")


# --- negative: the wrong branch must not be taken ---------------------------


@pytest.mark.negative
def test_inner_loop_below_ceiling_is_not_terminal() -> None:
    """A below-ceiling inner-loop failure must not terminate the run."""
    decision = route(
        _state(inner=INNER_LOOP_CEILING - 1), failure_kind=FailureKind.INNER_LOOP
    )
    assert decision.terminal is None
    assert decision.outcome is None


@pytest.mark.negative
def test_e2e_below_ceiling_does_not_escalate() -> None:
    """A below-ceiling e2e failure must not escalate to HITL."""
    decision = route(_state(e2e=E2E_CEILING - 1), failure_kind=FailureKind.E2E)
    assert decision.escalate_to_hitl is False
    assert decision.next_node != HITL_NODE


@pytest.mark.negative
def test_inner_loop_at_ceiling_never_retries_developer() -> None:
    """At the inner ceiling the router must not retry the Developer."""
    decision = route(
        _state(inner=INNER_LOOP_CEILING), failure_kind=FailureKind.INNER_LOOP
    )
    assert decision.next_node != DEVELOPER_NODE
