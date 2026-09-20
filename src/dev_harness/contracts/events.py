"""IPC envelope and discriminated payload union for all 9 event types (V11 0.6).

Each EventType maps 1:1 to a payload model carrying a Literal ``type`` tag so
the Envelope discriminates on it and rejects type/payload mismatches.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dev_harness.contracts.enums import CriticCommand, EventType, FailureClass
from dev_harness.contracts.state import HarnessState


class FileChangePayload(BaseModel):
    """A tracked file changed on disk (producer: 5.11)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["FILE_CHANGE"]
    path: str
    change_type: Literal["modified", "created", "deleted"]


class GitStatusUpdatePayload(BaseModel):
    """Git status changed (producer: 5.11)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["GIT_STATUS_UPDATE"]
    branch: str
    dirty_count: int


class AgentTokenStreamPayload(BaseModel):
    """A streamed token from an agent (producer: 3.9)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["AGENT_TOKEN_STREAM"]
    seq: int
    token: str
    agent: str = ""


class TestProgressPayload(BaseModel):
    """Test progress update (producer: 8.11)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["TEST_PROGRESS"]
    chunk_id: str
    passed: int
    failed: int
    total: int
    failure_class: FailureClass | None = None


class ModelConfigChangePayload(BaseModel):
    """The active model changed (producer: 3.7 / 5.5)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["MODEL_CONFIG_CHANGE"]
    provider: str
    model: str


class InterruptRequestPayload(BaseModel):
    """A critic command request (producer: 7.6 / 4.7)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["INTERRUPT_REQUEST"]
    command: CriticCommand
    reason: str = ""


class InterruptAckPayload(BaseModel):
    """Acknowledgment of a critic command (producer: 6.2)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["INTERRUPT_ACK"]
    command: CriticCommand
    already: bool = False


class MetricsUpdatePayload(BaseModel):
    """Rate-limit and cost metrics (producer: 4.10)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["METRICS_UPDATE"]
    p50_latency_ms: float
    p95_latency_ms: float
    tpm_burn: int
    cumulative_usd: float


class SnapshotPayload(BaseModel):
    """Full HarnessState as the first frame on attach (producer: 5.8)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["SNAPSHOT"]
    state: HarnessState


EventPayload = Annotated[
    Union[
        FileChangePayload,
        GitStatusUpdatePayload,
        AgentTokenStreamPayload,
        TestProgressPayload,
        ModelConfigChangePayload,
        InterruptRequestPayload,
        InterruptAckPayload,
        MetricsUpdatePayload,
        SnapshotPayload,
    ],
    Field(discriminator="type"),
]


class Envelope(BaseModel):
    """The framed IPC envelope (V11 0.6).

    The envelope ``type`` must match the payload's discriminated ``type`` tag;
    a mismatch is a validation error.
    """

    model_config = ConfigDict(extra="forbid")

    type: EventType
    seq: int = 0
    payload: EventPayload

    @model_validator(mode="after")
    def _enforce_type_match(self) -> "Envelope":
        if self.type.value != self.payload.type:
            raise ValueError(
                f"envelope type {self.type.value!r} does not match "
                f"payload type {self.payload.type!r}"
            )
        return self


# Public mapping from EventType to payload model, used by the ownership checker
# and the IPC CLI send-all command.
PAYLOAD_BY_TYPE: dict[EventType, type[BaseModel]] = {
    EventType.FILE_CHANGE: FileChangePayload,
    EventType.GIT_STATUS_UPDATE: GitStatusUpdatePayload,
    EventType.AGENT_TOKEN_STREAM: AgentTokenStreamPayload,
    EventType.TEST_PROGRESS: TestProgressPayload,
    EventType.MODEL_CONFIG_CHANGE: ModelConfigChangePayload,
    EventType.INTERRUPT_REQUEST: InterruptRequestPayload,
    EventType.INTERRUPT_ACK: InterruptAckPayload,
    EventType.METRICS_UPDATE: MetricsUpdatePayload,
    EventType.SNAPSHOT: SnapshotPayload,
}
