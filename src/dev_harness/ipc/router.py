"""Pub/sub router: subscribe by EventType, fan-out, handler isolation (V11 2.5).

A raising handler must not block other subscribers, and the error is logged
exactly once.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope

Subscriber = Callable[[Envelope], None]


class EventRouter:
    """Routes envelopes to subscribers registered by EventType."""

    def __init__(self) -> None:
        self._subs: dict[EventType, list[Subscriber]] = defaultdict(list)
        self._logger = logging.getLogger("dev_harness.ipc.router")

    def subscribe(self, event_type: EventType, handler: Subscriber) -> None:
        """Register a handler for an event type."""
        self._subs[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: Subscriber) -> None:
        """Remove a handler for an event type (no-op if absent)."""
        if handler in self._subs[event_type]:
            self._subs[event_type].remove(handler)

    def publish(self, envelope: Envelope) -> None:
        """Fan out an envelope to all subscribers of its type.

        A raising handler is logged once and does not block others.
        """
        for handler in list(self._subs[envelope.type]):
            try:
                handler(envelope)
            except Exception:
                self._logger.exception("handler for %s raised", envelope.type.value)

    def subscriber_count(self, event_type: EventType) -> int:
        """Number of subscribers for an event type."""
        return len(self._subs[event_type])
