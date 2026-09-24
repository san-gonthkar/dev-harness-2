"""IPC -> UI bridge tests (V11 task 7.7).

Unit tests drive ``_apply`` directly; the integration test marshals 5,000
cross-thread envelopes through a real ``BackpressureQueue`` under ``run_test``.
No ``time.sleep`` — waits are bounded by a deadline and yield via ``pilot.pause``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from textual._context import NoActiveAppError

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import (
    AgentTokenStreamPayload,
    Envelope,
    GitStatusUpdatePayload,
    MetricsUpdatePayload,
    ModelConfigChangePayload,
    SnapshotPayload,
)
from dev_harness.contracts.events import (
    TestProgressPayload as ProgressPayload,
)
from dev_harness.contracts.state import HarnessState
from dev_harness.ipc.queue import BackpressureQueue
from dev_harness.tui.app import HermesApp
from dev_harness.tui.bridge import Bridge

SIZE = (100, 30)
#: Bounded wait for the reader thread to drain the queue.
DRAIN_DEADLINE_S = 30.0


def make_state() -> HarnessState:
    """A minimal valid HarnessState for SNAPSHOT payloads."""
    return HarnessState(project_id="p1", workspace_path="/w", thread_id="t1")


def snapshot_env(seq: int = 0) -> Envelope:
    return Envelope(
        type=EventType.SNAPSHOT,
        seq=seq,
        payload=SnapshotPayload(type="SNAPSHOT", state=make_state()),
    )


def token_env(seq: int) -> Envelope:
    return Envelope(
        type=EventType.AGENT_TOKEN_STREAM,
        seq=seq,
        payload=AgentTokenStreamPayload(type="AGENT_TOKEN_STREAM", seq=seq, token="tok"),
    )


def git_env(seq: int) -> Envelope:
    return Envelope(
        type=EventType.GIT_STATUS_UPDATE,
        seq=seq,
        payload=GitStatusUpdatePayload(type="GIT_STATUS_UPDATE", branch="main", dirty_count=0),
    )


def progress_env(seq: int) -> Envelope:
    return Envelope(
        type=EventType.TEST_PROGRESS,
        seq=seq,
        payload=ProgressPayload(
            type="TEST_PROGRESS", chunk_id="c1", passed=1, failed=0, total=1
        ),
    )


def model_env(seq: int) -> Envelope:
    return Envelope(
        type=EventType.MODEL_CONFIG_CHANGE,
        seq=seq,
        payload=ModelConfigChangePayload(
            type="MODEL_CONFIG_CHANGE", provider="anthropic", model="m1"
        ),
    )


def metrics_env(seq: int) -> Envelope:
    return Envelope(
        type=EventType.METRICS_UPDATE,
        seq=seq,
        payload=MetricsUpdatePayload(
            type="METRICS_UPDATE",
            p50_latency_ms=1.0,
            p95_latency_ms=2.0,
            tpm_burn=10,
            cumulative_usd=0.01,
        ),
    )


class _FakeApp:
    """A stand-in app whose ``call_from_thread`` runs inline or raises."""

    def __init__(self, *, raise_no_active: bool = False) -> None:
        self.raise_no_active = raise_no_active

    def call_from_thread(self, callback: Callable[..., Any], *args: Any) -> Any:
        if self.raise_no_active:
            raise NoActiveAppError()
        return callback(*args)


# --- unit -------------------------------------------------------------------


@pytest.mark.unit
def test_on_registers_and_dispatches_by_type() -> None:
    """Handlers fire only for their registered event type."""
    bridge = Bridge(_FakeApp(), source=lambda: None)  # type: ignore[arg-type]
    seen: list[EventType] = []
    bridge.on(EventType.GIT_STATUS_UPDATE, lambda env: seen.append(env.type))
    bridge.on(EventType.GIT_STATUS_UPDATE, lambda env: seen.append(env.type))

    bridge._apply(git_env(1))
    bridge._apply(token_env(2))

    assert seen == [EventType.GIT_STATUS_UPDATE, EventType.GIT_STATUS_UPDATE]
    assert bridge.applied == 2
    assert bridge.control_applied == 1


@pytest.mark.unit
def test_snapshot_sets_state_and_flag() -> None:
    """A SNAPSHOT sets ``state`` and ``snapshot_seen``."""
    bridge = Bridge(_FakeApp(), source=lambda: None)  # type: ignore[arg-type]
    assert bridge.state is None
    assert bridge.snapshot_seen is False

    bridge._apply(snapshot_env())

    assert bridge.snapshot_seen is True
    assert bridge.state is not None
    assert bridge.state.project_id == "p1"
    assert bridge.applied == 1
    assert bridge.control_applied == 1


@pytest.mark.unit
def test_ordering_preserved_snapshot_before_deltas() -> None:
    """Fed SNAPSHOT-first, the state is set before any delta is applied."""
    bridge = Bridge(_FakeApp(), source=lambda: None)  # type: ignore[arg-type]
    order: list[EventType] = []
    bridge.on(EventType.SNAPSHOT, lambda env: order.append(env.type))
    bridge.on(EventType.AGENT_TOKEN_STREAM, lambda env: order.append(env.type))

    for env in (snapshot_env(), token_env(1), token_env(2)):
        bridge._apply(env)

    assert order == [
        EventType.SNAPSHOT,
        EventType.AGENT_TOKEN_STREAM,
        EventType.AGENT_TOKEN_STREAM,
    ]
    assert bridge.applied == 3
    assert bridge.control_applied == 1


@pytest.mark.unit
def test_source_callable_and_queue_are_equivalent() -> None:
    """A BackpressureQueue passed as ``queue`` is accepted as the source."""
    queue = BackpressureQueue()
    queue.put(git_env(1))
    bridge = Bridge(_FakeApp(), queue=queue)  # type: ignore[arg-type]
    assert bridge.dropped == 0
    env = queue.get()
    assert env is not None
    bridge._apply(env)
    assert bridge.applied == 1


@pytest.mark.unit
def test_queue_passed_as_source_is_accepted() -> None:
    """A BackpressureQueue passed as ``source`` is treated as the queue."""
    queue = BackpressureQueue()
    queue.put(git_env(1))
    bridge = Bridge(_FakeApp(), source=queue)  # type: ignore[arg-type]
    assert bridge.dropped == 0
    env = queue.get()
    assert env is not None
    bridge._apply(env)
    assert bridge.applied == 1


@pytest.mark.unit
def test_requires_a_source() -> None:
    """Constructing without a source or queue is a programming error."""
    with pytest.raises(ValueError, match="requires a source"):
        Bridge(_FakeApp())  # type: ignore[arg-type]


@pytest.mark.unit
def test_start_is_idempotent_and_stop_joins() -> None:
    """A second ``start`` is a no-op; ``stop`` joins the single thread."""
    bridge = Bridge(_FakeApp(), source=lambda: None)  # type: ignore[arg-type]
    bridge.start()
    first = bridge._thread
    bridge.start()
    assert bridge._thread is first
    bridge.stop(timeout=2.0)
    assert bridge._thread is None
    assert bridge.applied == 0


@pytest.mark.unit
def test_no_active_app_is_recorded_as_dropped() -> None:
    """A ``NoActiveAppError`` from the app is counted, not raised."""
    bridge = Bridge(_FakeApp(raise_no_active=True), source=lambda: token_env(1))  # type: ignore[arg-type]
    bridge.start()
    bridge.stop(timeout=2.0)
    assert bridge.dropped == 1
    assert bridge.applied == 0


# --- negative ---------------------------------------------------------------


@pytest.mark.negative
def test_delta_before_snapshot_does_not_raise() -> None:
    """A delta arriving before any SNAPSHOT is applied without error."""
    bridge = Bridge(_FakeApp(), source=lambda: None)  # type: ignore[arg-type]
    bridge._apply(token_env(1))
    assert bridge.applied == 1
    assert bridge.snapshot_seen is False
    assert bridge.state is None


@pytest.mark.negative
def test_stop_on_never_started_bridge_is_noop() -> None:
    """``stop`` before ``start`` does nothing and does not raise."""
    bridge = Bridge(_FakeApp(), source=lambda: None)  # type: ignore[arg-type]
    bridge.stop(timeout=0.1)
    assert bridge._thread is None


@pytest.mark.negative
def test_stop_is_idempotent() -> None:
    """Calling ``stop`` twice is safe."""
    bridge = Bridge(_FakeApp(), source=lambda: None)  # type: ignore[arg-type]
    bridge.start()
    bridge.stop(timeout=2.0)
    bridge.stop(timeout=2.0)
    assert bridge._thread is None


# --- integration ------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_5000_cross_thread_events() -> None:
    """5,000 cross-thread envelopes: 0 NoActiveAppError, 0 dropped control events."""
    app = HermesApp()
    queue = BackpressureQueue()
    total = 5000
    control_total = 5  # SNAPSHOT + 4 control events

    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        bridge = Bridge(app, queue=queue)
        order: list[EventType] = []
        snapshot_applied = threading.Event()
        controls_applied = threading.Event()
        control_seen = 0

        def on_snapshot(env: Envelope) -> None:
            order.append(env.type)
            snapshot_applied.set()

        def on_control(env: Envelope) -> None:
            nonlocal control_seen
            control_seen += 1
            if control_seen >= control_total - 1:
                controls_applied.set()

        bridge.on(EventType.SNAPSHOT, on_snapshot)
        bridge.on(EventType.AGENT_TOKEN_STREAM, lambda env: order.append(env.type))
        for control_type in (
            EventType.GIT_STATUS_UPDATE,
            EventType.TEST_PROGRESS,
            EventType.MODEL_CONFIG_CHANGE,
            EventType.METRICS_UPDATE,
        ):
            bridge.on(control_type, on_control)
        bridge.start()

        produced = threading.Event()

        def produce() -> None:
            # Backpressure-aware: the queue drops oldest on token overflow, so
            # control events are only enqueued once the bridge has consumed the
            # SNAPSHOT (and then the controls) — guaranteeing 0 control drops.
            queue.put(snapshot_env(0))
            snapshot_applied.wait(DRAIN_DEADLINE_S)
            queue.put(git_env(1))
            queue.put(progress_env(2))
            queue.put(model_env(3))
            queue.put(metrics_env(4))
            controls_applied.wait(DRAIN_DEADLINE_S)
            for i in range(total - control_total):
                queue.put(token_env(5 + i))
            produced.set()

        producer = threading.Thread(target=produce, name="producer")
        producer.start()

        deadline = time.monotonic() + DRAIN_DEADLINE_S
        while not produced.is_set() and time.monotonic() < deadline:
            await pilot.pause()
        assert produced.is_set(), "producer did not finish within the deadline"

        expected = total - queue.dropped_frames
        while bridge.applied < expected and time.monotonic() < deadline:
            await pilot.pause()

        bridge.stop(timeout=2.0)
        producer.join(timeout=2.0)

        assert bridge.applied == expected
        assert bridge.control_applied == control_total
        assert bridge.dropped == queue.dropped_frames  # no marshal drops
        assert bridge.snapshot_seen is True
        assert bridge.state is not None
        assert order[0] is EventType.SNAPSHOT