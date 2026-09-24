"""V7 system state as Pydantic v2 models (V11 0.5).

Reconciles critic_gatekeeper_status to the 4-value ExecutionState enum so the
schema can persist STOPPED (V11 audit C2).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dev_harness.contracts.enums import ChunkStatus, ExecutionState, PanelId

#: Canonical E2E failure classifications (V11 8.17). These are the single source
#: of the two ``E2EReport.classification`` values; the classifier imports them
#: rather than repeating the strings (no state literals outside ``contracts``).
CHUNK_IMPLEMENTATION_BUG: Literal["CHUNK_IMPLEMENTATION_BUG"] = (
    "CHUNK_IMPLEMENTATION_BUG"
)
INTEGRATION_SPEC_MISMATCH: Literal["INTEGRATION_SPEC_MISMATCH"] = (
    "INTEGRATION_SPEC_MISMATCH"
)


class GroomedRequirements(BaseModel):
    """Locked groomed requirements (V7 schema)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["LOCKED"] = "LOCKED"
    prd_content: str
    version: str = "V7"
    locked_at_timestamp: int


class InterfaceContracts(BaseModel):
    """OpenAPI spec and DB schema produced by the architect."""

    model_config = ConfigDict(extra="forbid")

    openapi_spec: str = ""
    db_schema: str = ""


class TechnicalDesign(BaseModel):
    """Architect output (V7 schema)."""

    model_config = ConfigDict(extra="forbid")

    architecture_spec: str = ""
    interface_contracts: InterfaceContracts = Field(default_factory=InterfaceContracts)
    status: Literal["APPROVED", "PENDING", "REJECTED"] = "PENDING"


class Chunk(BaseModel):
    """A chunk in the DAG (V7 schema)."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    title: str
    dependencies: list[str] = Field(default_factory=list)
    status: ChunkStatus = ChunkStatus.PENDING
    assigned_worker_id: str | None = None


class TuiState(BaseModel):
    """TUI display state (V7 schema)."""

    model_config = ConfigDict(extra="forbid")

    active_panel: PanelId = PanelId.CANVAS
    active_model_provider: str = ""
    is_paused: bool = False
    last_interrupt_timestamp: int | None = None
    critic_gatekeeper_status: ExecutionState = ExecutionState.READY


class GitState(BaseModel):
    """Git state (V7 schema)."""

    model_config = ConfigDict(extra="forbid")

    active_branch: str = "main"
    last_checkpoint_commit: str | None = None


class RateLimiting(BaseModel):
    """Rate-limit state (V7 schema)."""

    model_config = ConfigDict(extra="forbid")

    allocated_tokens_tpm: int = 0
    current_rpm_count: int = 0


class E2EReport(BaseModel):
    """End-to-end test report (V7 schema)."""

    model_config = ConfigDict(extra="forbid")

    classification: (
        Literal["CHUNK_IMPLEMENTATION_BUG", "INTEGRATION_SPEC_MISMATCH"] | None
    ) = None
    stack_trace: str = ""
    failed_chunk_id: str | None = None


class HarnessState(BaseModel):
    """The unified V7 system state (V11 0.5)."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    workspace_path: str
    thread_id: str
    raw_input: str = ""
    groomed_requirements: GroomedRequirements | None = None
    technical_design: TechnicalDesign | None = None
    chunk_dag: list[Chunk] = Field(default_factory=list)
    tui_state: TuiState = Field(default_factory=TuiState)
    git_state: GitState = Field(default_factory=GitState)
    rate_limiting: RateLimiting = Field(default_factory=RateLimiting)
    inner_loop_retry_count: int = 0
    e2e_retry_count: int = 0
    latest_e2e_report: E2EReport | None = None
