"""Run artifact store tests (V11 0.15)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_harness.observability.artifacts import RunArtifactStore

pytestmark = pytest.mark.unit

API_KEY = "sk-ant-api03-ABCDEF1234567890XYZ"


def test_three_turn_run_writes_ordered_entries(tmp_path: Path) -> None:
    store = RunArtifactStore(tmp_path / "run.jsonl")
    store.append({"kind": "prompt", "seq": 1, "message": "first"})
    store.append({"kind": "completion", "seq": 2, "message": "second"})
    store.append({"kind": "diff", "seq": 3, "message": "third"})
    entries = store.entries()
    assert [e["seq"] for e in entries] == [1, 2, 3]
    assert [e["kind"] for e in entries] == ["prompt", "completion", "diff"]


def test_cap_rotates_without_losing_newest(tmp_path: Path) -> None:
    # Very small cap so rotation triggers.
    store = RunArtifactStore(tmp_path / "run.jsonl", cap_bytes=60)
    for i in range(10):
        store.append({"seq": i, "message": "x" * 20})
    entries = store.entries()
    assert len(entries) >= 1
    # The newest entry must always be present.
    assert entries[-1]["seq"] == 9


def test_entries_redacted(tmp_path: Path) -> None:
    store = RunArtifactStore(tmp_path / "run.jsonl")
    store.append({"kind": "completion", "message": f"secret {API_KEY}"})
    entries = store.entries()
    assert API_KEY not in entries[0]["message"]
    assert "***REDACTED***" in entries[0]["message"]
