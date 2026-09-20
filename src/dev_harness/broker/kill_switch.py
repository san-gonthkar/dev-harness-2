"""Budget kill-switch (V11 4.7).

When the cost governor breaches a ceiling, the kill-switch emits exactly one
INTERRUPT_REQUEST{STOP, reason=BUDGET} to the reservation's callback_endpoint
and refuses further reservations. The callback_endpoint is the engine's
socket path (ADR-0002); the daemon routes the envelope to that endpoint.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from dev_harness.broker.cost import BudgetExceededError
from dev_harness.contracts.enums import CriticCommand, EventType
from dev_harness.contracts.events import Envelope, InterruptRequestPayload

Emit = Callable[[Envelope], None]


class KillSwitch:
    """Emits a single STOP/BUDGET interrupt and refuses further reservations."""

    def __init__(self, emit: Emit) -> None:
        self._emit = emit
        self._lock = threading.Lock()
        self._tripped = False
        self._emitted = 0

    @property
    def tripped(self) -> bool:
        """Whether the kill-switch has tripped."""
        with self._lock:
            return self._tripped

    @property
    def emitted(self) -> int:
        """Number of STOP/BUDGET interrupts emitted (0 or 1)."""
        with self._lock:
            return self._emitted

    def trip(self, callback_endpoint: str) -> None:
        """Trip the switch: emit exactly one STOP/BUDGET to the endpoint.

        The callback_endpoint is carried in the payload reason context; the
        daemon routes the envelope to that socket path.
        """
        with self._lock:
            if self._tripped:
                return
            self._tripped = True
            self._emitted += 1
        self._emit(
            Envelope(
                type=EventType.INTERRUPT_REQUEST,
                payload=InterruptRequestPayload(
                    type="INTERRUPT_REQUEST",
                    command=CriticCommand.STOP,
                    reason=f"BUDGET:{callback_endpoint}",
                ),
            )
        )

    def check(self) -> None:
        """Raise BudgetExceededError if the switch has tripped."""
        if self.tripped:
            raise BudgetExceededError(
                "budget kill-switch tripped; reservations refused",
                remediation="Raise the budget ceiling or restart the broker.",
            )
