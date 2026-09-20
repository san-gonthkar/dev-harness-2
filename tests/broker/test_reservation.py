"""Reservation protocol tests (V11 4.3) — leak prevention."""

from __future__ import annotations

import pytest

from dev_harness.broker.reservation import ReservationStore
from dev_harness.contracts.enums import ProviderId


class FrozenClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.unit
def test_client_killed_post_reserve_releases_at_ttl_plus_1s() -> None:
    clock = FrozenClock()
    store = ReservationStore(clock=clock, ttl=120.0)
    res = store.create(ProviderId.ANTHROPIC, 1.0, "/tmp/engine.sock")
    assert store.live_count() == 1
    # Client dies without commit/release. At TTL+1s the reservation is gone.
    clock.advance(121.0)
    assert store.get(res.reservation_id) is None
    assert store.live_count() == 0


@pytest.mark.unit
def test_commit_actual_less_than_reserved_returns_delta() -> None:
    clock = FrozenClock()
    store = ReservationStore(clock=clock)
    res = store.create(ProviderId.ANTHROPIC, 10.0, "/tmp/engine.sock")
    delta = store.commit(res.reservation_id, 4.0)
    assert delta == 6.0
    # The reservation is marked committed.
    updated = store.get(res.reservation_id)
    assert updated is not None
    assert updated.committed is True


@pytest.mark.unit
def test_callback_endpoint_present_on_every_reservation() -> None:
    clock = FrozenClock()
    store = ReservationStore(clock=clock)
    res = store.create(ProviderId.ANTHROPIC, 1.0, "/tmp/engine.sock")
    assert res.callback_endpoint == "/tmp/engine.sock"
    assert res.callback_endpoint != ""


@pytest.mark.unit
def test_release_returns_tokens() -> None:
    clock = FrozenClock()
    store = ReservationStore(clock=clock)
    res = store.create(ProviderId.ANTHROPIC, 5.0, "/tmp/engine.sock")
    tokens = store.release(res.reservation_id)
    assert tokens == 5.0
    assert store.live_count() == 0


@pytest.mark.unit
def test_release_unknown_returns_zero() -> None:
    clock = FrozenClock()
    store = ReservationStore(clock=clock)
    assert store.release("nope") == 0.0


@pytest.mark.unit
def test_reap_expired_returns_expired() -> None:
    clock = FrozenClock()
    store = ReservationStore(clock=clock, ttl=10.0)
    store.create(ProviderId.ANTHROPIC, 1.0, "/tmp/engine.sock")
    clock.advance(11.0)
    expired = store.reap_expired()
    assert len(expired) == 1
    assert store.live_count() == 0


@pytest.mark.unit
def test_all_returns_live_snapshot() -> None:
    clock = FrozenClock()
    store = ReservationStore(clock=clock)
    store.create(ProviderId.ANTHROPIC, 1.0, "/tmp/engine.sock")
    store.create(ProviderId.OLLAMA, 1.0, "/tmp/engine.sock")
    assert len(store.all()) == 2
