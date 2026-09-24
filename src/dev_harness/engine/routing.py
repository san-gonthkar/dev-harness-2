"""Retry router: bounded inner-loop and e2e retries with HITL escalation (V11 8.13).

The SDLC graph has two nested retry loops. The **inner loop** re-runs the
Developer -> Tester cycle when a chunk's tests fail; the **e2e loop** re-runs
the whole pipeline when the end-to-end suite fails. Both are bounded so a
failing requirement can never spin forever (8.D step 7: "terminal state
``FAILED``, never a loop"):

* the inner loop retries at most :data:`INNER_LOOP_CEILING` (3) times; on the
  **4th attempt** - i.e. once ``inner_loop_retry_count`` reaches the ceiling -
  the router routes **back to the Architect** to re-plan;
* the e2e loop retries at most :data:`E2E_CEILING` (2) times; once
  ``e2e_retry_count`` reaches the ceiling the router **escalates to HITL** and
  the run **terminates** in ``ExecutionState.STOPPED`` with outcome
  ``RunOutcome.FAILED``.

The decision is a **pure function** of the state channels (``state ->
RouteDecision``): it reads only the two retry counters, holds no clock and no
randomness, and returns the next node name or a terminal marker. That makes it
trivially testable and deterministic, and it is what a LangGraph conditional
edge calls.

``ExecutionState`` is the 4-value lifecycle vocabulary and has no ``FAILED``
member (V11 0.5), so the terminal *outcome* is the canonical
:class:`~dev_harness.contracts.enums.RunOutcome` enum - no string literals.

``engine/routing.py`` is in the 8.C high-coverage set (95/90): every branch of
every ceiling (below / at / above, inner and e2e) is exercised by the tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from dev_harness.contracts.enums import ExecutionState, RunOutcome
from dev_harness.engine.state import HarnessStateChannels

#: Maximum inner-loop retries before the router re-plans via the Architect.
INNER_LOOP_CEILING = 3

#: Maximum e2e retries before the router escalates to HITL and terminates.
E2E_CEILING = 2

#: Graph node names the router may route to (not state vocabulary).
ARCHITECT_NODE = "architect"
DEVELOPER_NODE = "developer"
HITL_NODE = "hitl"


class FailureKind(str, Enum):
    """Which retry loop observed the failure (V11 8.13)."""

    INNER_LOOP = "INNER_LOOP"
    E2E = "E2E"


@dataclass(frozen=True)
class RouteDecision:
    """The router's pure decision for one failure (V11 8.13).

    :param next_node: the node to route to, or ``None`` when the run ends.
    :param terminal: the terminal lifecycle state, or ``None`` to continue.
    :param outcome: the terminal run outcome, or ``None`` to continue.
    :param escalate_to_hitl: whether the decision hands control to the HITL gate.
    """

    next_node: str | None = None
    terminal: ExecutionState | None = None
    outcome: RunOutcome | None = None
    escalate_to_hitl: bool = False


def route(state: HarnessStateChannels, *, failure_kind: FailureKind) -> RouteDecision:
    """Decide the next node (or terminal) for a failure, purely from ``state``.

    :param state: the graph state channels; only the retry counters are read.
    :param failure_kind: which loop observed the failure.
    :returns: the :class:`RouteDecision` for this failure.
    """
    if failure_kind is FailureKind.INNER_LOOP:
        return _route_inner_loop(state)
    return _route_e2e(state)


def _route_inner_loop(state: HarnessStateChannels) -> RouteDecision:
    """Retry the Developer while below the ceiling, else re-plan via Architect."""
    count = state.get("inner_loop_retry_count", 0)
    if count < INNER_LOOP_CEILING:
        return RouteDecision(next_node=DEVELOPER_NODE)
    return RouteDecision(next_node=ARCHITECT_NODE)


def _route_e2e(state: HarnessStateChannels) -> RouteDecision:
    """Retry the Developer while below the ceiling, else escalate and terminate."""
    count = state.get("e2e_retry_count", 0)
    if count < E2E_CEILING:
        return RouteDecision(next_node=DEVELOPER_NODE)
    return RouteDecision(
        next_node=HITL_NODE,
        terminal=ExecutionState.STOPPED,
        outcome=RunOutcome.FAILED,
        escalate_to_hitl=True,
    )
