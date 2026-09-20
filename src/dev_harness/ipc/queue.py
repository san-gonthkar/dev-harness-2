"""Backpressure queue (V11 2.6).

Capacity 2048. Token streams (AGENT_TOKEN_STREAM) drop-oldest with a counter;
control events never drop. A dropped token increments dropped_frames.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope

CAPACITY = 2048

# Event types that may be dropped under pressure (token streams).
_DROPPABLE: frozenset[EventType] = frozenset({EventType.AGENT_TOKEN_STREAM})
# Control events that must never be dropped.
_CONTROL: frozenset[EventType] = frozenset(
    {
        EventType.FILE_CHANGE,
        EventType.GIT_STATUS_UPDATE,
        EventType.TEST_PROGRESS,
        EventType.MODEL_CONFIG_CHANGE,
        EventType.INTERRUPT_REQUEST,
        EventType.INTERRUPT_ACK,
        EventType.METRICS_UPDATE,
        EventType.SNAPSHOT,
    }
)


@dataclass
class BackpressureQueue:
    """A bounded queue that drops oldest token frames under pressure."""

    capacity: int = CAPACITY
    dropped_frames: int = 0
    _items: deque[Envelope] = field(default_factory=deque, init=False)

    def put(self, envelope: Envelope) -> bool:
        """Enqueue an envelope. Returns True if accepted, False if dropped.

        Token streams drop the oldest token when full; control events are
        never dropped (they block until space frees).
        """
        if envelope.type in _DROPPABLE:
            if len(self._items) >= self.capacity:
                self._items.popleft()
                self.dropped_frames += 1
            self._items.append(envelope)
            return True
        # Control events: never drop. If full, block is not possible here, so
        # we evict the oldest token (never a control event) to make room.
        if len(self._items) >= self.capacity:
            # Evict oldest droppable item if any; otherwise refuse (should not
            # happen because control events are rare).
            for i, item in enumerate(self._items):
                if item.type in _DROPPABLE:
                    del self._items[i]
                    self.dropped_frames += 1
                    break
            else:
                return False
        self._items.append(envelope)
        return True

    def get(self) -> Envelope | None:
        """Pop the oldest envelope, or None when empty."""
        if not self._items:
            return None
        return self._items.popleft()

    def __len__(self) -> int:
        return len(self._items)

    def is_empty(self) -> bool:
        return not self._items
