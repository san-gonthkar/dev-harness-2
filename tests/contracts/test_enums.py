"""Enum exhaustiveness and literal-ban tests (V11 0.3)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dev_harness.contracts.enums import (
    ChunkStatus,
    CriticCommand,
    EventType,
    ExecutionState,
    FailureClass,
    PanelId,
    ProviderId,
)

pytestmark = pytest.mark.unit

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def test_critic_command_exhaustive() -> None:
    assert set(CriticCommand) == {
        CriticCommand.START,
        CriticCommand.PAUSE,
        CriticCommand.RESUME,
        CriticCommand.STOP,
    }


def test_execution_state_has_stopped() -> None:
    assert ExecutionState.STOPPED in ExecutionState
    assert len(ExecutionState) == 4


def test_event_type_has_nine_members() -> None:
    assert len(EventType) == 9
    assert EventType.SNAPSHOT in EventType


def test_chunk_status_members() -> None:
    assert set(ChunkStatus) == {
        ChunkStatus.PENDING,
        ChunkStatus.IN_PROGRESS,
        ChunkStatus.COMPLETED,
        ChunkStatus.FAILED,
    }


def test_panel_id_members() -> None:
    assert set(PanelId) == {
        PanelId.WORKSPACE,
        PanelId.CANVAS,
        PanelId.REGISTRY,
        PanelId.CRITIC,
    }


def test_provider_id_members() -> None:
    assert set(ProviderId) == {
        ProviderId.ANTHROPIC,
        ProviderId.OPENROUTER,
        ProviderId.OLLAMA,
    }


def test_failure_class_members() -> None:
    assert FailureClass.TIMEOUT in FailureClass
    assert FailureClass.UNKNOWN in FailureClass


def test_no_string_literals_outside_enums() -> None:
    """State string literals must live only in enums.py (V11 0.3 literal ban)."""
    banned = {"READY", "RUNNING", "PAUSED", "STOPPED", "START", "RESUME"}
    pattern = re.compile(r'"(' + "|".join(banned) + r')"')
    offenders: list[str] = []
    for py in SRC_ROOT.rglob("*.py"):
        if py.name == "enums.py":
            continue
        text = py.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{py.relative_to(SRC_ROOT)}:{lineno}")
    assert offenders == [], f"state literals outside enums.py: {offenders}"
