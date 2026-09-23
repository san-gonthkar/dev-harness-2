"""State broadcast tests (V11 5.8).

Validation matrix: a client attaching to a paused session receives
``SNAPSHOT{state: PAUSED}`` as its first frame before any delta.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_harness.contracts.enums import EventType, ExecutionState
from dev_harness.contracts.events import (
    AgentTokenStreamPayload,
    Envelope,
    SnapshotPayload,
)
from dev_harness.contracts.state import HarnessState, TuiState
from dev_harness.engine.fanout import Fanout
from dev_harness.engine.state_broadcast import StateBroadcast

pytestmark = pytest.mark.unit


def _paused_state(workspace: str) -> HarnessState:
    """A HarnessState whose critic gatekeeper is PAUSED."""
    return HarnessState(
        project_id="ws-test",
        workspace_path=workspace,
        thread_id="t1",
        tui_state=TuiState(
            is_paused=True,
            critic_gatekeeper_status=ExecutionState.PAUSED,
        ),
    )


def _token(seq: int) -> Envelope:
    return Envelope(
        type=EventType.AGENT_TOKEN_STREAM,
        seq=seq,
        payload=AgentTokenStreamPayload(
            type="AGENT_TOKEN_STREAM", seq=seq, token=f"t{seq}"
        ),
    )


@pytest.mark.unit
def test_late_attach_receives_snapshot_first(tmp_path: Path) -> None:
    """A client attaching to a paused session gets SNAPSHOT{PAUSED} first."""
    state = _paused_state(str(tmp_path))
    fanout = Fanout()
    broadcast = StateBroadcast(fanout, state_of=lambda: state)

    fanout.attach("late")
    broadcast.on_attach("late")
    # A delta arrives after the attach.
    fanout.publish(_token(1))

    frames = fanout.drain("late")
    assert len(frames) == 2
    first = frames[0]
    assert first.type == EventType.SNAPSHOT
    assert isinstance(first.payload, SnapshotPayload)
    assert first.payload.state.tui_state.critic_gatekeeper_status == (
        ExecutionState.PAUSED
    )
    assert first.payload.state.tui_state.is_paused is True
    # The delta follows the snapshot.
    assert frames[1].type == EventType.AGENT_TOKEN_STREAM


@pytest.mark.unit
def test_snapshot_is_first_frame_before_any_delta() -> None:
    """The snapshot is enqueued before any delta published after attach."""
    state = _paused_state("/ws")
    fanout = Fanout()
    broadcast = StateBroadcast(fanout, state_of=lambda: state)

    fanout.attach("c")
    broadcast.on_attach("c")
    for i in range(5):
        fanout.publish(_token(i))

    frames = fanout.drain("c")
    assert frames[0].type == EventType.SNAPSHOT
    assert [f.type for f in frames[1:]] == [EventType.AGENT_TOKEN_STREAM] * 5


@pytest.mark.unit
def test_existing_clients_do_not_receive_duplicate_snapshot() -> None:
    """publish_to delivers the snapshot only to the attaching client."""
    state = _paused_state("/ws")
    fanout = Fanout()
    broadcast = StateBroadcast(fanout, state_of=lambda: state)

    fanout.attach("existing")
    fanout.attach("new")
    broadcast.on_attach("new")
    fanout.publish(_token(1))

    new_frames = fanout.drain("new")
    assert new_frames[0].type == EventType.SNAPSHOT
    existing_frames = fanout.drain("existing")
    # The existing client never sees the snapshot, only the delta.
    assert [f.type for f in existing_frames] == [EventType.AGENT_TOKEN_STREAM]


@pytest.mark.unit
def test_snapshot_carries_full_state() -> None:
    """The SNAPSHOT payload is the full HarnessState, not a delta."""
    state = _paused_state("/ws")
    fanout = Fanout()
    broadcast = StateBroadcast(fanout, state_of=lambda: state)

    fanout.attach("c")
    broadcast.on_attach("c")
    frames = fanout.drain("c")
    assert len(frames) == 1
    payload = frames[0].payload
    assert isinstance(payload, SnapshotPayload)
    assert payload.state == state
    assert payload.state.thread_id == "t1"
    assert payload.state.workspace_path == "/ws"


@pytest.mark.unit
def test_on_attach_unknown_client_is_noop() -> None:
    """on_attach for a client that was never attached is a no-op."""
    state = _paused_state("/ws")
    fanout = Fanout()
    broadcast = StateBroadcast(fanout, state_of=lambda: state)

    broadcast.on_attach("ghost")
    assert fanout.client_count() == 0
    assert fanout.drain("ghost") == []


@pytest.mark.unit
def test_snapshot_reflects_live_state_at_attach_time() -> None:
    """The snapshot reflects the state at the moment of attach."""
    fanout = Fanout()
    current = _paused_state("/ws")

    def state_of() -> HarnessState:
        return current

    broadcast = StateBroadcast(fanout, state_of=state_of)
    fanout.attach("c")
    broadcast.on_attach("c")
    frames = fanout.drain("c")
    assert isinstance(frames[0].payload, SnapshotPayload)
    assert frames[0].payload.state.tui_state.critic_gatekeeper_status == (
        ExecutionState.PAUSED
    )

    # The state changes to RUNNING; a new attach sees the new state.
    current = current.model_copy(
        update={
            "tui_state": current.tui_state.model_copy(
                update={"critic_gatekeeper_status": ExecutionState.RUNNING}
            )
        }
    )
    fanout.attach("c2")
    broadcast.on_attach("c2")
    frames2 = fanout.drain("c2")
    assert isinstance(frames2[0].payload, SnapshotPayload)
    assert frames2[0].payload.state.tui_state.critic_gatekeeper_status == (
        ExecutionState.RUNNING
    )
