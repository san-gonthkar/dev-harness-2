"""Reducer semantics for the LangGraph state channels (V11 8.3).

Validation matrix (8.B): parallel ``chunk_dag`` appends -> 2 entries;
concurrent retry increments -> exactly +2. The reducers are exercised
directly (no compiled graph required).
"""

from __future__ import annotations

from typing import Annotated, Any, get_args, get_origin, get_type_hints

import pytest

from dev_harness.contracts.enums import ChunkStatus
from dev_harness.contracts.state import Chunk, GitState, TuiState
from dev_harness.engine.state import (
    HarnessStateChannels,
    add_counters,
    append_chunks,
)

pytestmark = pytest.mark.unit


def _reducer(channel: str) -> Any:
    """Return the single reducer attached to a channel, or ``None``."""
    hints = get_type_hints(HarnessStateChannels, include_extras=True)
    annotation = hints[channel]
    if get_origin(annotation) is not Annotated:
        return None
    metadata = get_args(annotation)[1:]
    return metadata[0] if metadata else None


def _merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update using each channel's reducer (or overwrite)."""
    merged = dict(base)
    for key, value in update.items():
        reducer = _reducer(key)
        if reducer is not None and key in merged:
            merged[key] = reducer(merged[key], value)
        else:
            merged[key] = value
    return merged


def _chunk(chunk_id: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        title=chunk_id,
        dependencies=[],
        status=ChunkStatus.PENDING,
        assigned_worker_id=None,
    )


@pytest.mark.unit
def test_append_chunks_yields_two_entries() -> None:
    """Two parallel chunk_dag writes yield 2 entries (not last-write-wins)."""
    assert append_chunks([_chunk("a")], [_chunk("b")]) == [
        _chunk("a"),
        _chunk("b"),
    ]


@pytest.mark.unit
def test_parallel_chunk_dag_writes_append() -> None:
    """Merging two partial chunk_dag updates appends to 2 entries."""
    base = {"chunk_dag": [_chunk("a")]}
    merged = _merge(base, {"chunk_dag": [_chunk("b")]})
    assert [c.chunk_id for c in merged["chunk_dag"]] == ["a", "b"]


@pytest.mark.unit
def test_add_counters_sums() -> None:
    """add_counters sums its operands."""
    assert add_counters(1, 1) == 2


@pytest.mark.unit
@pytest.mark.parametrize("channel", ["inner_loop_retry_count", "e2e_retry_count"])
def test_concurrent_retry_increments_are_exactly_plus_two(
    channel: str,
) -> None:
    """Two concurrent increments on a retry counter yield exactly +2."""
    merged: dict[str, Any] = {channel: 0}
    merged = _merge(merged, {channel: 1})
    merged = _merge(merged, {channel: 1})
    assert merged[channel] == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    "channel",
    ["inner_loop_retry_count", "e2e_retry_count"],
)
def test_retry_counters_use_additive_reducer(channel: str) -> None:
    """The retry counter channels are wired to the additive reducer."""
    assert _reducer(channel) is add_counters


@pytest.mark.unit
def test_chunk_dag_uses_append_reducer() -> None:
    """The chunk_dag channel is wired to the append reducer."""
    assert _reducer("chunk_dag") is append_chunks


@pytest.mark.unit
@pytest.mark.parametrize(
    "channel",
    [
        "project_id",
        "workspace_path",
        "thread_id",
        "raw_input",
        "groomed_requirements",
        "technical_design",
        "tui_state",
        "git_state",
        "rate_limiting",
        "latest_e2e_report",
    ],
)
def test_scalar_channels_are_last_write_wins(channel: str) -> None:
    """Scalar channels carry no reducer (last-write-wins)."""
    assert _reducer(channel) is None


@pytest.mark.unit
def test_scalar_channel_overwrites_on_merge() -> None:
    """A scalar channel update replaces the previous value."""
    merged = _merge({"raw_input": "old"}, {"raw_input": "new"})
    assert merged["raw_input"] == "new"


@pytest.mark.unit
def test_reducer_annotation_shape() -> None:
    """chunk_dag is annotated with exactly one reducer; counters too."""
    hints = get_type_hints(HarnessStateChannels, include_extras=True)
    assert get_args(hints["chunk_dag"])[0] == list[Chunk]
    assert get_args(hints["inner_loop_retry_count"])[0] is int
    assert get_args(hints["e2e_retry_count"])[0] is int
    for channel in ("chunk_dag", "inner_loop_retry_count", "e2e_retry_count"):
        assert get_origin(hints[channel]) is Annotated
        assert len(get_args(hints[channel])) == 2


def test_tui_and_git_defaults_available() -> None:
    """The mirrored state models remain constructible for node writes."""
    assert TuiState().is_paused is False
    assert GitState().active_branch == "main"
