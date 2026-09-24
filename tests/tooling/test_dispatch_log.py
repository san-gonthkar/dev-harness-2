"""Tests for the dispatch telemetry log (process fix 1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import dispatch_log

pytestmark = pytest.mark.unit


@pytest.fixture
def log_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the log to a tmp file."""
    path = tmp_path / "dispatch_log.jsonl"
    monkeypatch.setattr(dispatch_log, "LOG_PATH", path)
    return path


def test_start_returns_id_and_writes_record(log_path: Path) -> None:
    dispatch_id = dispatch_log.start("7.12b", "python-developer")
    assert dispatch_id
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert records[0]["event"] == "start"
    assert records[0]["task"] == "7.12b"
    assert records[0]["phase"] == "7"


def test_end_pairs_with_start_and_computes_duration(log_path: Path) -> None:
    dispatch_id = dispatch_log.start("7.12b", "python-developer")
    assert dispatch_log.end(dispatch_id, outcome="success", commit="abc", tool_calls=18)
    rows = dispatch_log._paired()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "success"
    assert rows[0]["commit"] == "abc"
    assert rows[0]["tool_calls"] == 18
    assert rows[0]["duration_s"] >= 0.0


def test_end_unknown_id_returns_false(log_path: Path) -> None:
    assert dispatch_log.end("nope", outcome="success") is False


def test_end_rejects_unknown_outcome(log_path: Path) -> None:
    dispatch_id = dispatch_log.start("7.12b", "python-developer")
    with pytest.raises(ValueError, match="unknown outcome"):
        dispatch_log.end(dispatch_id, outcome="banana")


def test_summary_aggregates_by_phase_and_outcome(log_path: Path) -> None:
    a = dispatch_log.start("7.1", "python-developer")
    dispatch_log.end(a, outcome="success", tool_calls=10)
    b = dispatch_log.start("7.2", "python-developer")
    dispatch_log.end(b, outcome="empty_return", tool_calls=25)
    result = dispatch_log.summary("7")
    assert result["dispatches"] == 2
    assert result["by_outcome"] == {"success": 1, "empty_return": 1}
    assert result["failures"] == 1
    assert result["by_phase"]["7"]["tool_calls"] == 35


def test_summary_empty_log(log_path: Path) -> None:
    result = dispatch_log.summary()
    assert result["dispatches"] == 0
    assert result["failures"] == 0


def test_main_start_end_roundtrip(log_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert dispatch_log.main(["start", "--task", "7.3", "--agent", "python-developer"]) == 0
    dispatch_id = capsys.readouterr().out.strip()
    assert dispatch_log.main(["end", "--id", dispatch_id, "--outcome", "success"]) == 0
    assert dispatch_log.main(["summary"]) == 0
    assert json.loads(capsys.readouterr().out)["dispatches"] == 1


def test_main_end_unknown_id_exits_1(log_path: Path) -> None:
    assert dispatch_log.main(["end", "--id", "missing", "--outcome", "success"]) == 1