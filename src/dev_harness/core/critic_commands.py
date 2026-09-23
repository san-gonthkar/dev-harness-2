"""Idempotent critic command handler (V11 6.2).

The handler is the engine-side entry point for critic commands arriving over
IPC (``INTERRUPT_REQUEST``). It delegates to the :class:`CriticGatekeeper`
(6.1) for the normative 0.22 transition table and emits an ``INTERRUPT_ACK``
envelope for every accepted command — including idempotent repeats, which
carry ``already=True`` (PAUSE while PAUSED, STOP while STOPPED).

An illegal command raises :class:`IllegalTransitionError` and emits no ACK;
the gatekeeper state is left unchanged.
"""

from __future__ import annotations

from collections.abc import Callable

from dev_harness.contracts.enums import CriticCommand, EventType, ExecutionState
from dev_harness.contracts.events import Envelope, InterruptAckPayload
from dev_harness.core.critic import CriticGatekeeper

# A sink for emitted envelopes (the engine daemon passes ``Fanout.publish``).
AckSink = Callable[[Envelope], None]


class CriticCommandHandler:
    """Applies critic commands idempotently and acknowledges each one.

    ``emit`` is an ``Envelope`` sink (e.g. ``Fanout.publish``); every accepted
    command produces exactly one ``INTERRUPT_ACK`` envelope. Idempotent
    repeats (PAUSE while PAUSED, STOP while STOPPED) still emit an ACK with
    ``already=True`` and perform no state transition.
    """

    def __init__(
        self,
        gatekeeper: CriticGatekeeper | None = None,
        emit: AckSink | None = None,
    ) -> None:
        self._gatekeeper = gatekeeper if gatekeeper is not None else CriticGatekeeper()
        self._emit = emit
        self._seq = 0

    @property
    def state(self) -> ExecutionState:
        """The gatekeeper's current execution state."""
        return self._gatekeeper.state

    def handle(self, command: CriticCommand) -> Envelope:
        """Apply ``command`` and return the emitted ``INTERRUPT_ACK`` envelope.

        The transition is applied first; only a legal command reaches the ACK
        emission, so an illegal command raises :class:`IllegalTransitionError`
        without emitting anything.
        """
        result = self._gatekeeper.transition(command)
        envelope = Envelope(
            type=EventType.INTERRUPT_ACK,
            seq=self._seq,
            payload=InterruptAckPayload(
                type="INTERRUPT_ACK",
                command=command,
                already=result.already,
            ),
        )
        self._seq += 1
        if self._emit is not None:
            self._emit(envelope)
        return envelope
