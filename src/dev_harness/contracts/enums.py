"""Canonical enums for the Dev Harness.

Single source of truth for every state vocabulary in the system (V11 plan 0.3).
Never write a state string literal outside this module - the literal-ban test
greps for it.
"""

from __future__ import annotations

from enum import Enum


class ExecutionState(str, Enum):
    """Lifecycle state of a session (V7 schema, reconciled to 4 values)."""

    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class CriticCommand(str, Enum):
    """Commands accepted by the critic gatekeeper (V11 0.22 transition table)."""

    START = "START"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STOP = "STOP"


class ChunkStatus(str, Enum):
    """Status of a chunk in the DAG (V7 schema)."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PanelId(str, Enum):
    """The four TUI panels (V7 schema)."""

    WORKSPACE = "WORKSPACE"
    CANVAS = "CANVAS"
    REGISTRY = "REGISTRY"
    CRITIC = "CRITIC"


class EventType(str, Enum):
    """The closed event vocabulary (V11 2.5, 9 members)."""

    FILE_CHANGE = "FILE_CHANGE"
    GIT_STATUS_UPDATE = "GIT_STATUS_UPDATE"
    AGENT_TOKEN_STREAM = "AGENT_TOKEN_STREAM"
    TEST_PROGRESS = "TEST_PROGRESS"
    MODEL_CONFIG_CHANGE = "MODEL_CONFIG_CHANGE"
    INTERRUPT_REQUEST = "INTERRUPT_REQUEST"
    INTERRUPT_ACK = "INTERRUPT_ACK"
    METRICS_UPDATE = "METRICS_UPDATE"
    SNAPSHOT = "SNAPSHOT"


class ProviderId(str, Enum):
    """Supported LLM providers (V7 spec 5.1)."""

    ANTHROPIC = "anthropic"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"


class FailureClass(str, Enum):
    """Classification of a test failure (V11 8.11, first use of FailureClass)."""

    TIMEOUT = "TIMEOUT"
    TEST_FAILURE = "TEST_FAILURE"
    COMPILE_ERROR = "COMPILE_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    UNKNOWN = "UNKNOWN"
