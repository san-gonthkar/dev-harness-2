"""State broadcast: SNAPSHOT as the first frame on attach (V11 5.8).

A late-attaching client must render exactly what the other clients see.
The daemon therefore delivers a ``SNAPSHOT`` envelope carrying the full
``HarnessState`` (decision 2026-09-20) into the attaching client's queue
*before* any delta event can reach it. The fanout (5.4) owns per-client
queues; this module owns the ordering guarantee: snapshot first, then
deltas.

The snapshot is delivered through ``publish_to`` so existing clients never
receive a duplicate SNAPSHOT — only the newly-attached client does.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope, SnapshotPayload
from dev_harness.contracts.state import HarnessState


class StateBroadcast:
    """Delivers the current state as the first frame to attaching clients.

    ``state_of`` is a callable returning the current ``HarnessState`` (the
    daemon's live session state); ``fanout`` is the 5.4 fanout whose
    per-client queues carry the envelope stream. The snapshot is enqueued
    into the attaching client's queue before any subsequent delta, so a
    client that attaches to a paused session receives
    ``SNAPSHOT{state: PAUSED}`` as its first frame.
    """

    def __init__(
        self,
        fanout: Any,
        *,
        state_of: Callable[[], HarnessState],
    ) -> None:
        self._fanout = fanout
        self._state_of = state_of

    def snapshot_for(self, client_id: str) -> Envelope:
        """Build the SNAPSHOT envelope for the current state."""
        return Envelope(
            type=EventType.SNAPSHOT,
            payload=SnapshotPayload(
                type="SNAPSHOT",
                state=self._state_of(),
            ),
        )

    def on_attach(self, client_id: str) -> None:
        """Deliver the SNAPSHOT as the client's first frame.

        Called by the daemon right after ``fanout.attach(client_id)`` so the
        snapshot is enqueued before any delta published afterwards.
        """
        self._fanout.publish_to(client_id, self.snapshot_for(client_id))
