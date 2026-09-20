"""Fan-out tests (V11 5.4).

Validation matrix: 3 clients receive all 100 events in order; killing client 2
leaves clients 1 and 3 gapless.
"""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import AgentTokenStreamPayload, Envelope
from dev_harness.engine.fanout import Fanout

pytestmark = pytest.mark.unit


def _token(seq: int) -> Envelope:
    return Envelope(
        type=EventType.AGENT_TOKEN_STREAM,
        seq=seq,
        payload=AgentTokenStreamPayload(type="AGENT_TOKEN_STREAM", seq=seq, token=f"t{seq}"),
    )


@pytest.mark.unit
def test_three_clients_receive_all_events_in_order() -> None:
    """3 clients each receive all 100 events in order."""
    fanout = Fanout()
    for cid in ("c1", "c2", "c3"):
        fanout.attach(cid)

    for i in range(100):
        fanout.publish(_token(i))

    for cid in ("c1", "c2", "c3"):
        events = fanout.drain(cid)
        assert len(events) == 100
        assert [e.seq for e in events] == list(range(100))
        assert [e.payload.seq for e in events] == list(range(100))  # type: ignore[union-attr]


@pytest.mark.unit
def test_killing_client_2_leaves_1_and_3_gapless() -> None:
    """Detaching client 2 mid-stream leaves clients 1 and 3 gapless."""
    fanout = Fanout()
    for cid in ("c1", "c2", "c3"):
        fanout.attach(cid)

    for i in range(40):
        fanout.publish(_token(i))

    fanout.detach("c2")

    for i in range(40, 100):
        fanout.publish(_token(i))

    for cid in ("c1", "c3"):
        events = fanout.drain(cid)
        assert [e.payload.seq for e in events] == list(range(100))  # type: ignore[union-attr]

    # Client 2's queue was dropped on detach.
        assert fanout.drain("c2") == []


@pytest.mark.unit
def test_detach_is_independent() -> None:
    """Detaching one client does not affect the others' queues."""
    fanout = Fanout()
    fanout.attach("a")
    fanout.attach("b")
    fanout.publish(_token(1))
    fanout.detach("a")
    fanout.publish(_token(2))

    assert fanout.drain("a") == []
    assert [e.payload.seq for e in fanout.drain("b")] == [1, 2]  # type: ignore[union-attr]


@pytest.mark.unit
def test_attach_is_idempotent() -> None:
    """Attaching the same client twice does not duplicate its queue."""
    fanout = Fanout()
    fanout.attach("a")
    fanout.attach("a")
    fanout.publish(_token(1))
    assert fanout.client_count() == 1
    assert [e.payload.seq for e in fanout.drain("a")] == [1]  # type: ignore[union-attr]


@pytest.mark.unit
def test_detach_unknown_client_is_noop() -> None:
    """Detaching a client that was never attached is a no-op."""
    fanout = Fanout()
    fanout.attach("a")
    fanout.detach("nope")
    assert fanout.client_count() == 1


@pytest.mark.unit
def test_pending_counts() -> None:
    """pending() reports queued envelopes per client."""
    fanout = Fanout()
    fanout.attach("a")
    fanout.attach("b")
    fanout.publish(_token(1))
    fanout.publish(_token(2))
    assert fanout.pending("a") == 2
    assert fanout.pending("b") == 2
    fanout.drain("a")
    assert fanout.pending("a") == 0
    assert fanout.pending("b") == 2


@pytest.mark.unit
def test_drain_unknown_client_returns_empty() -> None:
    """drain() on an unknown client returns an empty list."""
    fanout = Fanout()
    assert fanout.drain("ghost") == []
    assert fanout.pending("ghost") == 0


@pytest.mark.unit
def test_clients_iteration() -> None:
    """clients() yields the attached client ids."""
    fanout = Fanout()
    fanout.attach("x")
    fanout.attach("y")
    assert sorted(fanout.clients()) == ["x", "y"]
    assert fanout.client_count() == 2


@pytest.mark.unit
def test_publish_with_no_clients_is_noop() -> None:
    """Publishing with no attached clients is a no-op."""
    fanout = Fanout()
    fanout.publish(_token(1))
    assert fanout.client_count() == 0