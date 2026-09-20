"""Multi-client fan-out with independent detach (V11 5.4).

Each attached client owns a BackpressureQueue. Every published envelope is
enqueued into every attached queue in order. Detaching one client drops only
its queue ? the remaining clients keep receiving every event, gapless.
"""

from __future__ import annotations

from collections.abc import Iterable

from dev_harness.contracts.events import Envelope
from dev_harness.ipc.queue import BackpressureQueue


class Fanout:
    """Fan envelopes out to per-client queues with independent detach."""

    def __init__(self) -> None:
        self._queues: dict[str, BackpressureQueue] = {}

    def attach(self, client_id: str) -> None:
        """Attach a client; it starts receiving every published envelope."""
        if client_id not in self._queues:
            self._queues[client_id] = BackpressureQueue()

    def detach(self, client_id: str) -> None:
        """Detach a client; its queue is dropped, others are unaffected."""
        self._queues.pop(client_id, None)

    def publish(self, envelope: Envelope) -> None:
        """Enqueue an envelope into every attached client's queue."""
        for queue in self._queues.values():
            queue.put(envelope)

    def drain(self, client_id: str) -> list[Envelope]:
        """Return and clear all pending envelopes for a client."""
        queue = self._queues.get(client_id)
        if queue is None:
            return []
        out: list[Envelope] = []
        while True:
            item = queue.get()
            if item is None:
                return out
            out.append(item)

    def pending(self, client_id: str) -> int:
        """Number of envelopes queued for a client."""
        queue = self._queues.get(client_id)
        return len(queue) if queue is not None else 0

    def clients(self) -> Iterable[str]:
        """The currently attached client ids."""
        return self._queues.keys()

    def client_count(self) -> int:
        """Number of attached clients."""
        return len(self._queues)
