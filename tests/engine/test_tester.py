"""Tester node tests (V11 8.11).

Validation matrix (8.B, acceptance is exact) against a REAL temp git repo:

(a) a PASSING suite yields the correct ``passed`` / ``failed`` / ``total`` counts;
(b) a HANGING test is KILLED at the configured timeout and reported with
    ``failure_class=TIMEOUT`` (NOT ``TEST_FAILURE``);
(c) on failure a ``TEST_PROGRESS`` envelope is emitted carrying the
    ``FailureClass``.

The child suite is a real subprocess (``python -m pytest``); the hanging test is
a real ``time.sleep`` **inside that child**, bounded by the node's timeout - the
test body itself never sleeps and asserts the node returns within a bounded
wall-clock. No network.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from dev_harness.contracts.enums import ChunkStatus, EventType, FailureClass
from dev_harness.contracts.events import Envelope, TestProgressPayload
from dev_harness.contracts.state import Chunk
from dev_harness.engine.nodes.tester import (
    TesterNode,
    classify_failure,
    parse_pytest_counts,
    run_test_command,
)
from dev_harness.engine.state import HarnessStateChannels
from dev_harness.engine.worker_workspace import WorkerWorkspace

# Neutralize any inherited pytest config (the worktree contains a copy of the
# repo, so a bare ``pytest`` would collect the whole suite) and keep the child
# run quiet and cache-free. Each test appends the one file it wrote.
CHILD_CMD: tuple[str, ...] = (
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-o",
    "addopts=",
    "-p",
    "no:cacheprovider",
)


def _cmd(test_file: str) -> tuple[str, ...]:
    """The child command scoped to exactly one test file in the worktree."""
    return (*CHILD_CMD, test_file)


# Bounded wall-clock for the hang test: well above the node timeout but far
# below the child's 30s sleep, so a failure to kill is unmistakable.
_HANG_BOUND_S = 15.0


def _chunk(worker_id: str = "worker-1") -> Chunk:
    return Chunk(
        chunk_id="c1",
        title="Implement the parser",
        assigned_worker_id=worker_id,
    )


def _state() -> HarnessStateChannels:
    return {
        "project_id": "p1",
        "workspace_path": "/tmp/ws",
        "thread_id": "t1",
    }


def _bind_and_write(ws: WorkerWorkspace, chunk: Chunk, rel: str, content: str) -> Path:
    """Bind the chunk's worker and write a file into the worktree root."""
    root = ws.bind(chunk.assigned_worker_id or "", chunk)
    path = ws.write_path(chunk, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return root


@pytest.mark.unit
def test_parse_pytest_counts_reads_summary_line() -> None:
    """Counts come from the last pytest summary line; absent counts are zero."""
    output = (
        "..F\n=== short test summary info ===\n1 failed, 2 passed, 1 error in 0.12s\n"
    )

    assert parse_pytest_counts(output) == (2, 1, 1)
    assert parse_pytest_counts("2 passed in 0.01s") == (2, 0, 0)
    assert parse_pytest_counts("no summary here") == (0, 0, 0)


@pytest.mark.unit
def test_classify_failure_timeout_beats_exit_code() -> None:
    """TIMEOUT takes precedence; errors map to COMPILE_ERROR, assertions to TEST_FAILURE."""
    assert (
        classify_failure(exit_code=None, timed_out=True, failed=0, errors=0)
        is FailureClass.TIMEOUT
    )
    assert classify_failure(exit_code=0, timed_out=False, failed=0, errors=0) is None
    assert (
        classify_failure(exit_code=1, timed_out=False, failed=2, errors=0)
        is FailureClass.TEST_FAILURE
    )
    assert (
        classify_failure(exit_code=2, timed_out=False, failed=0, errors=1)
        is FailureClass.COMPILE_ERROR
    )
    assert (
        classify_failure(exit_code=3, timed_out=False, failed=0, errors=0)
        is FailureClass.UNKNOWN
    )


@pytest.mark.integration
def test_passing_suite_reports_counts(tmp_workspace: Path) -> None:
    """(a): a passing suite yields correct passed/failed/total counts."""
    ws = WorkerWorkspace(tmp_workspace)
    chunk = _chunk()
    _bind_and_write(
        ws, chunk, "test_sample.py", "def test_ok():\n    assert 1 + 1 == 2\n"
    )
    node = TesterNode(ws, chunk, command=_cmd("test_sample.py"), timeout=60.0)

    update = node(_state())

    outcome = node.outcome
    assert outcome is not None
    assert (outcome.passed, outcome.failed, outcome.total) == (1, 0, 1)
    assert outcome.failure_class is None
    assert outcome.ok
    assert chunk.status is ChunkStatus.COMPLETED
    assert update["chunk_dag"] == [chunk]


@pytest.mark.integration
def test_hanging_test_is_killed_and_classified_timeout(tmp_workspace: Path) -> None:
    """(b): a hanging test is killed at the timeout and reported TIMEOUT."""
    ws = WorkerWorkspace(tmp_workspace)
    chunk = _chunk()
    _bind_and_write(
        ws,
        chunk,
        "test_hang.py",
        "import time\n\n\ndef test_hangs():\n    time.sleep(30)\n",
    )
    node = TesterNode(ws, chunk, command=_cmd("test_hang.py"), timeout=2.0)

    started = time.monotonic()
    node(_state())
    elapsed = time.monotonic() - started

    outcome = node.outcome
    assert outcome is not None
    assert outcome.timed_out is True
    assert outcome.exit_code is None
    assert outcome.failure_class is FailureClass.TIMEOUT
    assert outcome.failure_class is not FailureClass.TEST_FAILURE
    assert elapsed < _HANG_BOUND_S
    assert chunk.status is ChunkStatus.FAILED


@pytest.mark.integration
def test_failing_suite_emits_test_progress_with_failure_class(
    tmp_workspace: Path,
) -> None:
    """(c): on failure a TEST_PROGRESS envelope carries the FailureClass."""
    ws = WorkerWorkspace(tmp_workspace)
    chunk = _chunk()
    _bind_and_write(ws, chunk, "test_fail.py", "def test_bad():\n    assert 1 == 2\n")
    envelopes: list[Envelope] = []
    node = TesterNode(
        ws, chunk, command=_cmd("test_fail.py"), timeout=60.0, emit=envelopes.append
    )

    node(_state())

    assert len(envelopes) == 1
    envelope = envelopes[0]
    assert envelope.type is EventType.TEST_PROGRESS
    payload = envelope.payload
    assert isinstance(payload, TestProgressPayload)
    assert payload.chunk_id == "c1"
    assert payload.failed == 1
    assert payload.total == 1
    assert payload.failure_class is FailureClass.TEST_FAILURE
    assert chunk.status is ChunkStatus.FAILED


@pytest.mark.negative
def test_missing_command_classifies_runtime_error(tmp_workspace: Path) -> None:
    """A command that cannot start is a RUNTIME_ERROR, not a crash."""
    ws = WorkerWorkspace(tmp_workspace)
    chunk = _chunk()
    ws.bind("worker-1", chunk)
    node = TesterNode(
        ws, chunk, command=("definitely-not-a-real-binary-xyz",), timeout=5.0
    )

    node(_state())

    outcome = node.outcome
    assert outcome is not None
    assert outcome.exit_code == 127
    assert outcome.failure_class is FailureClass.RUNTIME_ERROR
    assert chunk.status is ChunkStatus.FAILED


@pytest.mark.unit
def test_run_test_command_reports_not_found(tmp_workspace: Path) -> None:
    """run_test_command returns exit 127 for a missing binary."""
    output, exit_code, timed_out = run_test_command(
        ("definitely-not-a-real-binary-xyz",), cwd=tmp_workspace, timeout=5.0
    )

    assert "command not found" in output
    assert exit_code == 127
    assert timed_out is False


@pytest.mark.unit
def test_second_run_is_noop(tmp_workspace: Path) -> None:
    """A completed run makes a second call a no-op (resume safety)."""
    ws = WorkerWorkspace(tmp_workspace)
    chunk = _chunk()
    _bind_and_write(ws, chunk, "test_sample.py", "def test_ok():\n    assert True\n")
    node = TesterNode(ws, chunk, command=_cmd("test_sample.py"), timeout=60.0)

    node(_state())
    update = node(_state())

    assert update == {}
