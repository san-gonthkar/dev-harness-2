"""Reservation protocol: reserve -> commit(actual) | release (V11 4.3).

A reservation holds capacity for a provider for up to ``TTL`` seconds. It
carries a ``callback_endpoint`` (the engine's socket path) so the budget
kill-switch knows where to route INTERRUPT_REQUEST (ADR-0002). If the client
dies without committing or releasing, the reservation expires at TTL+1s and
the capacity returns to the bucket (leak prevention).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from dev_harness.contracts.enums import ProviderId

TTL_SECONDS = 120.0
Clock = Callable[[], float]


@dataclass(frozen=True)
class Reservation:
    """A granted capacity reservation."""

    reservation_id: str
    provider: ProviderId
    tokens: float
    callback_endpoint: str
    created_at: float
    expires_at: float
    committed: bool = False
    released: bool = False


class ReservationStore:
    """Tracks live reservations and reaps expired ones."""

    def __init__(
        self, *, clock: Clock = time.monotonic, ttl: float = TTL_SECONDS
    ) -> None:
        self._clock = clock
        self.ttl = ttl
        self._reservations: dict[str, Reservation] = {}
        self._lock = threading.Lock()

    def create(
        self,
        provider: ProviderId,
        tokens: float,
        callback_endpoint: str,
    ) -> Reservation:
        """Create a reservation with a fresh id and TTL expiry."""
        now = self._clock()
        res = Reservation(
            reservation_id=uuid.uuid4().hex,
            provider=provider,
            tokens=tokens,
            callback_endpoint=callback_endpoint,
            created_at=now,
            expires_at=now + self.ttl,
        )
        with self._lock:
            self._reservations[res.reservation_id] = res
        return res

    def get(self, reservation_id: str) -> Reservation | None:
        """Look up a reservation (None if absent or expired)."""
        with self._lock:
            res = self._reservations.get(reservation_id)
            if res is None:
                return None
            if self._clock() > res.expires_at:
                self._reservations.pop(reservation_id, None)
                return None
            return res

    def commit(self, reservation_id: str, actual: float) -> float:
        """Mark a reservation committed; returns the unused token delta.

        The delta (reserved - actual) is returned to the caller so it can be
        released back to the bucket.
        """
        with self._lock:
            res = self._reservations.get(reservation_id)
            if res is None:
                return 0.0
            delta = max(0.0, res.tokens - actual)
            self._reservations[reservation_id] = Reservation(
                reservation_id=res.reservation_id,
                provider=res.provider,
                tokens=res.tokens,
                callback_endpoint=res.callback_endpoint,
                created_at=res.created_at,
                expires_at=res.expires_at,
                committed=True,
                released=res.released,
            )
            return delta

    def release(self, reservation_id: str) -> float:
        """Release a reservation; returns the tokens to return to the bucket."""
        with self._lock:
            res = self._reservations.pop(reservation_id, None)
            if res is None:
                return 0.0
            return res.tokens

    def reap_expired(self) -> list[Reservation]:
        """Remove and return all expired reservations (leak prevention)."""
        now = self._clock()
        expired: list[Reservation] = []
        with self._lock:
            for rid, res in list(self._reservations.items()):
                if now > res.expires_at:
                    expired.append(res)
                    del self._reservations[rid]
        return expired

    def live_count(self) -> int:
        """Number of live (unexpired) reservations."""
        with self._lock:
            return len(self._reservations)

    def all(self) -> list[Reservation]:
        """Snapshot of all live reservations."""
        with self._lock:
            return list(self._reservations.values())
