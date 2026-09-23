"""StubbornRunner tests (V11 6.9, 6.D step 3).

Validation matrix: the runner spawns 3 grandchildren and traps SIGINT; a
plain SIGINT does not terminate it; killing the process group stops its
output file growing. The pure helpers (arg parsing, child argv, stop logic)
are unit-tested without spawning anything.

Every spawned process is reaped in a ``finally`` so no test can leak a
process or hang. Waits are event-driven: the runner echoes each write to
stdout, and a reader thread feeds a queue the test blocks on with a timeout.
No ``time.sleep``.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import IO

import pytest

from dev_harness.core.process_group import is_posix
from tests.support import stubborn_runner as sr

_RUNNER = str(Path(sr.__file__).resolve())
_READY = "children-ready"


class _LineReader:
    """Reads a subprocess's stdout lines into a queue (event-driven waits)."""

    def __init__(self, stream: IO[str]) -> None:
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self._thread.start()

    def _pump(self, stream: IO[str]) -> None:
        try:
            for line in stream:
                self._queue.put(line.rstrip("\n"))
        finally:
            self._queue.put(None)  # EOF sentinel

    def read_line(self, timeout: float) -> str | None:
        """The next line, or None on EOF/timeout."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def read_until(self, token: str, timeout: float) -> bool:
        """True once ``token`` is seen; False on EOF/timeout."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            line = self.read_line(remaining)
            if line is None:
                return False
            if line == token:
                return True

    def drain(self, timeout: float) -> list[str]:
        """Collect lines until EOF or ``timeout``; excludes the EOF sentinel."""
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return lines
            line = self.read_line(remaining)
            if line is None:
                return lines
            lines.append(line)


def _start_runner(
    output: Path, *, children: int, max_iterations: int
) -> tuple[subprocess.Popen[str], _LineReader]:
    """Start the runner with a piped stdout and a reader thread."""
    proc = subprocess.Popen(
        [
            sys.executable,
            _RUNNER,
            "--output",
            str(output),
            "--children",
            str(children),
            "--max-iterations",
            str(max_iterations),
        ],
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=is_posix(),
    )
    assert proc.stdout is not None
    return proc, _LineReader(proc.stdout)


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """Kill the runner's whole group (POSIX) or just it, then reap."""
    if proc.poll() is None:
        if is_posix():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive cleanup
        proc.kill()
        proc.wait(timeout=5)


def _line_count(path: Path) -> int:
    """Number of lines in ``path`` (0 when it does not exist yet)."""
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


def _group_alive(pgid: int) -> bool:
    """True while any process remains in ``pgid`` (``pgrep -g`` equivalent)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


# --- unit: pure helpers, no spawning ---------------------------------------


@pytest.mark.unit
def test_parse_args_defaults() -> None:
    """Defaults: 3 children, unbounded iterations, no new session."""
    args = sr.parse_args(["--output", "out.txt"])
    assert args.output == "out.txt"
    assert args.children == sr.DEFAULT_CHILDREN == 3
    assert args.max_iterations == 0
    assert args.stop_file is None
    assert args.new_session is False


@pytest.mark.unit
def test_parse_args_overrides() -> None:
    """Explicit flags override every default."""
    args = sr.parse_args(
        [
            "--output",
            "o",
            "--children",
            "5",
            "--max-iterations",
            "7",
            "--stop-file",
            "s",
            "--new-session",
        ]
    )
    assert args.children == 5
    assert args.max_iterations == 7
    assert args.stop_file == "s"
    assert args.new_session is True


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["-1", "x", "1.5"])
def test_parse_args_rejects_bad_counts(bad: str) -> None:
    """A negative or non-integer child count is rejected (fail fast)."""
    with pytest.raises(SystemExit):
        sr.parse_args(["--output", "o", "--children", bad])


@pytest.mark.unit
def test_child_argv_shape() -> None:
    """Each grandchild argv runs the child program with its own output file."""
    argv = sr.child_argv("out.txt.child0", "stop", 4)
    assert argv[0] == sys.executable
    assert argv[1] == "-c"
    assert argv[3] == "out.txt.child0"
    assert argv[4] == "stop"
    assert argv[5] == "4"


@pytest.mark.unit
def test_should_stop_is_bounded() -> None:
    """The write loop ends on the iteration cap or the stop file."""
    assert sr.should_stop(0, 0, None) is False  # unbounded, no stop file
    assert sr.should_stop(3, 3, None) is True  # iteration cap reached
    assert sr.should_stop(0, 0, "does-not-exist") is False
    assert sr.should_stop(0, 0, __file__) is True  # stop file exists


@pytest.mark.unit
def test_wait_children_ready_zero_is_immediate() -> None:
    """Zero children is trivially ready (no files to wait for)."""
    assert sr.wait_children_ready("unused", 0) is True


# --- negative: SIGINT alone does not terminate ------------------------------


@pytest.mark.skipif(not is_posix(), reason="SIGINT delivery is POSIX-only")
@pytest.mark.negative
def test_sigint_does_not_terminate_runner(tmp_path: Path) -> None:
    """The runner traps SIGINT, so a plain SIGINT leaves it alive."""
    output = tmp_path / "runner.out"
    proc, reader = _start_runner(output, children=0, max_iterations=100000)
    try:
        assert reader.read_until(_READY, timeout=5.0), "runner never became ready"
        proc.send_signal(signal.SIGINT)
        # Still writing after the SIGINT => the handler did not exit.
        assert reader.read_line(timeout=5.0) is not None, "runner stopped writing"
        assert proc.poll() is None
    finally:
        _kill_tree(proc)


# --- integration: 3 grandchildren, growth, then group kill ------------------


@pytest.mark.skipif(not is_posix(), reason="process groups are POSIX-only")
@pytest.mark.integration
def test_spawns_three_grandchildren_and_killpg_stops_growth(
    tmp_path: Path,
) -> None:
    """3 grandchildren spawn, the file grows, and killpg stops it growing."""
    output = tmp_path / "runner.out"
    proc, reader = _start_runner(output, children=3, max_iterations=100000)
    try:
        assert reader.read_until(_READY, timeout=5.0), "children never became ready"

        # The runner and all 3 grandchildren wrote to their own files.
        child_files: Iterable[Path] = (Path(f"{output}.child{i}") for i in range(3))
        assert all(_line_count(p) > 0 for p in child_files), "grandchild missing"
        assert _line_count(output) > 0

        # The whole tree is in the runner's process group.
        assert os.getpgid(proc.pid) == proc.pid

        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)

        # The group is gone and the output file stops growing (EOF on stdout).
        assert _group_alive(proc.pid) is False
        frozen = _line_count(output)
        reader.drain(timeout=2.0)  # reaches EOF, no hang
        assert _line_count(output) == frozen
    finally:
        _kill_tree(proc)
