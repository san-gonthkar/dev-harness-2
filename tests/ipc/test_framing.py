"""Framing codec tests (V11 2.2)."""

from __future__ import annotations

import io
import struct

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dev_harness.contracts.enums import CriticCommand, EventType
from dev_harness.contracts.events import (
    AgentTokenStreamPayload,
    Envelope,
    FileChangePayload,
    GitStatusUpdatePayload,
    InterruptAckPayload,
    InterruptRequestPayload,
    MetricsUpdatePayload,
    ModelConfigChangePayload,
    SnapshotPayload,
    TestProgressPayload,
)
from dev_harness.contracts.state import HarnessState
from dev_harness.ipc.framing import (
    GLOBAL_MAX_FRAME,
    MAX_SNAPSHOT_FRAME,
    MAX_STREAM_FRAME,
    PREFIX_LEN,
    FrameTooLargeError,
    IncompleteFrameError,
    decode_frame,
    encode,
    max_frame_for,
    read_frame,
)

pytestmark = pytest.mark.unit


def _state() -> HarnessState:
    return HarnessState(
        project_id="p1", workspace_path="/w", thread_id="t1", raw_input="hello"
    )


def _envelope(event_type: EventType) -> Envelope:
    if event_type == EventType.FILE_CHANGE:
        return Envelope(
            type=event_type,
            payload=FileChangePayload(
                type="FILE_CHANGE", path="/a", change_type="modified"
            ),
        )
    if event_type == EventType.GIT_STATUS_UPDATE:
        return Envelope(
            type=event_type,
            payload=GitStatusUpdatePayload(
                type="GIT_STATUS_UPDATE", branch="main", dirty_count=1
            ),
        )
    if event_type == EventType.AGENT_TOKEN_STREAM:
        return Envelope(
            type=event_type,
            payload=AgentTokenStreamPayload(
                type="AGENT_TOKEN_STREAM", seq=1, token="x"
            ),
        )
    if event_type == EventType.TEST_PROGRESS:
        return Envelope(
            type=event_type,
            payload=TestProgressPayload(
                type="TEST_PROGRESS", chunk_id="c", passed=1, failed=0, total=1
            ),
        )
    if event_type == EventType.MODEL_CONFIG_CHANGE:
        return Envelope(
            type=event_type,
            payload=ModelConfigChangePayload(
                type="MODEL_CONFIG_CHANGE", provider="ollama", model="qwen"
            ),
        )
    if event_type == EventType.INTERRUPT_REQUEST:
        return Envelope(
            type=event_type,
            payload=InterruptRequestPayload(
                type="INTERRUPT_REQUEST", command=CriticCommand.PAUSE
            ),
        )
    if event_type == EventType.INTERRUPT_ACK:
        return Envelope(
            type=event_type,
            payload=InterruptAckPayload(
                type="INTERRUPT_ACK", command=CriticCommand.PAUSE, already=True
            ),
        )
    if event_type == EventType.METRICS_UPDATE:
        return Envelope(
            type=event_type,
            payload=MetricsUpdatePayload(
                type="METRICS_UPDATE",
                p50_latency_ms=1.0,
                p95_latency_ms=2.0,
                tpm_burn=3,
                cumulative_usd=0.01,
            ),
        )
    return Envelope(
        type=event_type, payload=SnapshotPayload(type="SNAPSHOT", state=_state())
    )


@settings(max_examples=500, deadline=None)
@given(st.sampled_from(list(EventType)))
def test_hypothesis_round_trip(event_type: EventType) -> None:
    env = _envelope(event_type)
    frame = encode(env)
    decoded = decode_frame(frame)
    assert decoded == env


def test_prefix_is_4_bytes_be() -> None:
    env = _envelope(EventType.FILE_CHANGE)
    frame = encode(env)
    (length,) = struct.unpack(">I", frame[:PREFIX_LEN])
    assert length == len(frame) - PREFIX_LEN


def test_2mib_stream_frame_rejected() -> None:
    """A 2 MiB non-SNAPSHOT frame exceeds the 1 MiB streaming limit."""
    big = "x" * (2 * 1024 * 1024)
    env = Envelope(
        type=EventType.AGENT_TOKEN_STREAM,
        payload=AgentTokenStreamPayload(type="AGENT_TOKEN_STREAM", seq=1, token=big),
    )
    with pytest.raises(FrameTooLargeError):
        encode(env)


def test_16mib_snapshot_accepted() -> None:
    """A 16 MiB SNAPSHOT frame is accepted under its limit."""
    big = "x" * (16 * 1024 * 1024 - 4096)  # under the 16 MiB ceiling
    state = _state()
    state.raw_input = big
    env = Envelope(
        type=EventType.SNAPSHOT, payload=SnapshotPayload(type="SNAPSHOT", state=state)
    )
    frame = encode(env)
    assert len(frame) <= MAX_SNAPSHOT_FRAME
    decoded = decode_frame(frame)
    assert decoded.payload.state.raw_input == big


def test_17mib_snapshot_rejected() -> None:
    """A 17 MiB SNAPSHOT frame exceeds the 16 MiB limit."""
    big = "x" * (17 * 1024 * 1024)
    state = _state()
    state.raw_input = big
    env = Envelope(
        type=EventType.SNAPSHOT, payload=SnapshotPayload(type="SNAPSHOT", state=state)
    )
    with pytest.raises(FrameTooLargeError):
        encode(env)


def test_truncated_frame_incomplete() -> None:
    """A truncated frame raises IncompleteFrameError, no hang."""
    env = _envelope(EventType.FILE_CHANGE)
    frame = encode(env)
    truncated = frame[:-10]
    with pytest.raises(IncompleteFrameError):
        decode_frame(truncated)


def test_short_data_incomplete() -> None:
    with pytest.raises(IncompleteFrameError):
        decode_frame(b"\x00\x00")


def test_read_frame_round_trip() -> None:
    env = _envelope(EventType.METRICS_UPDATE)
    frame = encode(env)
    stream = io.BytesIO(frame)
    decoded = read_frame(stream)
    assert decoded == env


def test_read_frame_eof_before_any() -> None:
    with pytest.raises(IncompleteFrameError):
        read_frame(io.BytesIO(b""))


def test_read_frame_eof_mid_prefix() -> None:
    with pytest.raises(IncompleteFrameError):
        read_frame(io.BytesIO(b"\x00\x00"))


def test_read_frame_eof_mid_body() -> None:
    env = _envelope(EventType.FILE_CHANGE)
    frame = encode(env)
    stream = io.BytesIO(frame[: PREFIX_LEN + 5])
    with pytest.raises(IncompleteFrameError):
        read_frame(stream)


def test_read_frame_oversized_declared() -> None:
    # Declared length exceeds the global ceiling.
    prefix = struct.pack(">I", GLOBAL_MAX_FRAME + 1)
    with pytest.raises(FrameTooLargeError):
        read_frame(io.BytesIO(prefix))


def test_max_frame_for() -> None:
    assert max_frame_for(EventType.SNAPSHOT) == MAX_SNAPSHOT_FRAME
    assert max_frame_for(EventType.AGENT_TOKEN_STREAM) == MAX_STREAM_FRAME
    assert max_frame_for(EventType.FILE_CHANGE) == MAX_STREAM_FRAME
