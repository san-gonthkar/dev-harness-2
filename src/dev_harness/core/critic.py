"""Critic gatekeeper state machine (V11 6.1).

The gatekeeper is the single authority on the session's execution state. It
holds the current :class:`ExecutionState` and applies critic commands through
the normative 0.22 transition table (``contracts/transitions.py``), which is
the single source of truth for legal (state, command) pairs.

An illegal command raises :class:`IllegalTransitionError` carrying the
offending (state, command) pair; the gatekeeper's state is left unchanged.
"""

from __future__ import annotations

from dev_harness.contracts.enums import CriticCommand, ExecutionState
from dev_harness.contracts.transitions import TransitionResult, apply_transition


class CriticGatekeeper:
    """Stateful holder of the session's execution state.

    Starts in ``READY``. Each :meth:`transition` call consults the 0.22 table;
    legal transitions advance the state, no-ops and idempotent repeats return
    the ``already`` flag, and illegal pairs raise :class:`IllegalTransitionError`
    without mutating state.
    """

    def __init__(self, initial: ExecutionState = ExecutionState.READY) -> None:
        self._state = initial

    @property
    def state(self) -> ExecutionState:
        """The current execution state."""
        return self._state

    def transition(self, command: CriticCommand) -> TransitionResult:
        """Apply ``command`` per the 0.22 table.

        Returns the :class:`TransitionResult` (target state + ``already`` flag)
        on success. Raises :class:`IllegalTransitionError` (state unchanged)
        when the pair is not in the table.
        """
        result = apply_transition(self._state, command)
        # apply_transition raises on ILLEGAL cells; the check narrows the
        # union for the type checker and guards against future changes.
        if result.result != "ILLEGAL":
            self._state = result.result
        return result
