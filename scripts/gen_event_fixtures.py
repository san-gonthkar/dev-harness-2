"""Generate the 9 event fixtures for the P2 contract harness (V11 2.8)."""

from __future__ import annotations

from pathlib import Path

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

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "events"


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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for event_type in EventType:
        env = _envelope(event_type)
        path = OUT / f"{event_type.value.lower()}.json"
        path.write_text(env.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.name}")


if __name__ == "__main__":
    main()
