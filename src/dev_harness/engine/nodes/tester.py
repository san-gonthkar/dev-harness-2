"""Tester persona graph node: invocation, structured counts, timeout (V11 8.11).

The Tester is the fourth node of the SDLC pipeline: after the developer (8.10)
has written the chunk inside its isolated worker worktree, the tester runs that
chunk's test command **in the worktree root** and turns the raw subprocess
result into a structured verdict.

Three behaviors are load-bearing (8.B acceptance is exact):

* a **passing** suite yields the correct ``passed`` / ``failed`` / ``total``
  counts, parsed from pytest's summary line;
* a **hanging** test is **killed at the configured timeout** and classified
  :attr:`FailureClass.TIMEOUT` - never ``TEST_FAILURE`` (a timeout is not a
  failing assertion, and the pipeline routes it differently);
* on any failure a ``TEST_PROGRESS`` :class:`Envelope` carrying the
  :class:`FailureClass` is emitted, which gives ``FailureClass`` its first use
  and ``TEST_PROGRESS`` its producer (V11 A14).

The subprocess is started in its own process group (POSIX ``setsid``, Windows
``CREATE_NEW_PROCESS_GROUP``) so a timeout kills the whole tree - pytest may
have spawned worker subprocesses of its own. The envelope sink is injected
(``Callable[[Envelope], None]``) so tests capture envelopes without a socket.

The node is a sync LangGraph callable: it takes the state and returns a partial
update. It is idempotent - a second call after a completed run is a no-op, so a
resumed graph never re-runs (or re-bills) the suite.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from dev_harness.contracts.enums import ChunkStatus, EventType, FailureClass
from dev_harness.contracts.events import Envelope, TestProgressPayload
from dev_harness.contracts.state import Chunk
from dev_harness.engine.state import HarnessStateChannels
from dev_harness.engine.worker_workspace import WorkerWorkspace

# The default command runs the chunk's pytest suite quietly. It is injectable so
# a caller can pass the chunk's own test command.
DEFAULT_TEST_COMMAND: tuple[str, ...] = (sys.executable, "-m", "pytest", "-q")

# pytest's terminal summary line, e.g. "1 failed, 2 passed, 1 error in 0.12s".
_SUMMARY_MARKER = re.compile(r"\bin \d+(?:\.\d+)?s")
_PASSED_RE = re.compile(r"(\d+) passed")
_FAILED_RE = re.compile(r"(\d+) failed")
_ERRORS_RE = re.compile(r"(\d+) errors?")

# A timeout is not a test failure; the pipeline routes it to a retry, so it gets
# its own class rather than TEST_FAILURE.
_TIMEOUT_KILL_GRACE_S = 5.0


@dataclass(frozen=True)
class TestOutcome:
    """The structured result of one test-command invocation."""

    passed: int
    failed: int
    errors: int
    total: int
    exit_code: int | None
    failure_class: FailureClass | None
    timed_out: bool
    command: tuple[str, ...]
    output: str = field(repr=False)

    @property
    def ok(self) -> bool:
        """True when the suite passed (exit 0, no timeout, no failures)."""
        return self.failure_class is None


def parse_pytest_counts(output: str) -> tuple[int, int, int]:
    """Parse ``(passed, failed, errors)`` from a pytest terminal summary.

    Scans for the last summary line (one matching ``... in <n>s``) and reads the
    counts from it. Absent counts are ``0``, so a suite that ran nothing reports
    three zeros rather than raising.
    """
    passed = failed = errors = 0
    for line in output.splitlines():
        if not _SUMMARY_MARKER.search(line):
            continue
        passed_match = _PASSED_RE.search(line)
        failed_match = _FAILED_RE.search(line)
        errors_match = _ERRORS_RE.search(line)
        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        errors = int(errors_match.group(1)) if errors_match else 0
    return passed, failed, errors


def classify_failure(
    *, exit_code: int | None, timed_out: bool, failed: int, errors: int
) -> FailureClass | None:
    """Map a raw subprocess result to a :class:`FailureClass` (or ``None``).

    ``TIMEOUT`` takes precedence - a killed process has no meaningful exit code.
    A command that could not be started (exit 127) is a ``RUNTIME_ERROR``; a
    collection/import error with no failing assertion is a ``COMPILE_ERROR``; a
    failing assertion is a ``TEST_FAILURE``; anything else is ``UNKNOWN``.
    """
    if timed_out:
        return FailureClass.TIMEOUT
    if exit_code == 0:
        return None
    if exit_code == 127:
        return FailureClass.RUNTIME_ERROR
    if errors > 0 and failed == 0:
        return FailureClass.COMPILE_ERROR
    if failed > 0:
        return FailureClass.TEST_FAILURE
    return FailureClass.UNKNOWN


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """Kill the child and every process in its group/tree."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:  # pragma: no cover - exercised on POSIX CI only
        try:
            # POSIX-only names, absent from the Windows typeshed stubs.
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # type: ignore[attr-defined]
        except (ProcessLookupError, PermissionError):
            proc.kill()
    proc.wait(timeout=_TIMEOUT_KILL_GRACE_S)


def _start_process(command: Sequence[str], cwd: Path) -> subprocess.Popen[str]:
    """Start ``command`` in its own killable process group, capturing output."""
    args = list(command)
    if os.name == "nt":
        return subprocess.Popen(
            args,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    return subprocess.Popen(  # pragma: no cover - exercised on POSIX CI only
        args,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


def run_test_command(
    command: Sequence[str], *, cwd: Path, timeout: float
) -> tuple[str, int | None, bool]:
    """Run ``command`` in ``cwd`` with a bounded timeout.

    Returns ``(output, exit_code, timed_out)``. On timeout the process tree is
    killed and ``exit_code`` is ``None`` with ``timed_out`` True. A command that
    cannot be started (``FileNotFoundError``) is reported as ``exit_code`` 127
    so it classifies as a ``RUNTIME_ERROR`` rather than crashing the node.
    """
    try:
        proc = _start_process(command, cwd)
    except FileNotFoundError:
        return f"command not found: {command[0]}", 127, False
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        output, _ = proc.communicate()
        return output, None, True
    return output, proc.returncode, False


class TesterNode:
    """LangGraph node running the chunk's test command in its worktree.

    :param workspace: the worker-worktree binding; the command runs in
        ``workspace.root_for(chunk)``.
    :param chunk: the chunk under test.
    :param command: the test command; defaults to ``pytest -q``.
    :param timeout: wall-clock seconds before the suite is killed.
    :param emit: optional envelope sink (``Callable[[Envelope], None]``); when
        set, a ``TEST_PROGRESS`` envelope is emitted for every run.
    """

    # The name starts with "Test", which pytest would otherwise try to collect.
    __test__ = False

    def __init__(
        self,
        workspace: WorkerWorkspace,
        chunk: Chunk,
        *,
        command: Sequence[str] | None = None,
        timeout: float = 300.0,
        emit: Callable[[Envelope], None] | None = None,
    ) -> None:
        self._workspace = workspace
        self._chunk = chunk
        self._command = tuple(command) if command is not None else DEFAULT_TEST_COMMAND
        self._timeout = timeout
        self._emit = emit
        self._seq = 0
        self._outcome: TestOutcome | None = None

    @property
    def outcome(self) -> TestOutcome | None:
        """The outcome of the last run, or ``None`` before the first run."""
        return self._outcome

    def __call__(self, state: HarnessStateChannels) -> dict[str, list[Chunk]]:
        """Run the suite; return the chunk with its post-test status.

        A second call after a completed run is a no-op (resume safety). On a
        pass the chunk is ``COMPLETED``; on any failure it is ``FAILED``.
        """
        del state  # the tester reads only its own chunk and worktree
        if self._outcome is not None:
            return {}
        root = self._workspace.root_for(self._chunk)
        output, exit_code, timed_out = run_test_command(
            self._command, cwd=root, timeout=self._timeout
        )
        passed, failed, errors = parse_pytest_counts(output)
        failure_class = classify_failure(
            exit_code=exit_code, timed_out=timed_out, failed=failed, errors=errors
        )
        self._outcome = TestOutcome(
            passed=passed,
            failed=failed,
            errors=errors,
            total=passed + failed + errors,
            exit_code=exit_code,
            failure_class=failure_class,
            timed_out=timed_out,
            command=self._command,
            output=output,
        )
        self._emit_progress(self._outcome)
        self._chunk.status = (
            ChunkStatus.COMPLETED if self._outcome.ok else ChunkStatus.FAILED
        )
        return {"chunk_dag": [self._chunk]}

    def _emit_progress(self, outcome: TestOutcome) -> None:
        """Emit one ``TEST_PROGRESS`` envelope carrying the counts + class."""
        if self._emit is None:
            return
        envelope = Envelope(
            type=EventType.TEST_PROGRESS,
            seq=self._seq,
            payload=TestProgressPayload(
                type="TEST_PROGRESS",
                chunk_id=self._chunk.chunk_id,
                passed=outcome.passed,
                failed=outcome.failed,
                total=outcome.total,
                failure_class=outcome.failure_class,
            ),
        )
        self._seq += 1
        self._emit(envelope)


def make_tester_node(
    workspace: WorkerWorkspace,
    chunk: Chunk,
    *,
    command: Sequence[str] | None = None,
    timeout: float = 300.0,
    emit: Callable[[Envelope], None] | None = None,
) -> TesterNode:
    """Build the tester node bound to a worktree and an optional emit sink."""
    return TesterNode(workspace, chunk, command=command, timeout=timeout, emit=emit)
