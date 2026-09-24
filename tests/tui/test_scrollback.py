"""Scrollback cap + spill tests (V11 task 7.4).

The smoke-lane tests exercise the bounding/spill/ordered-retrieval logic with a
small line count. The 200k-line memory soak is ``@pytest.mark.slow`` (NIGHTLY)
and is deselected in the smoke lane.

No ``time.sleep`` — the soak is bounded by line count, not wall-clock waiting.
"""

from __future__ import annotations

import tracemalloc
from pathlib import Path

import pytest

from dev_harness.contracts.errors import HarnessError, ScrollbackError
from dev_harness.observability.artifacts import RunArtifactStore
from dev_harness.tui.scrollback import ScrollbackBuffer, _FileSink

#: Content that ``redact()`` leaves untouched (no secret-shaped substrings).
SAFE = "line {i:06d} :: token stream chunk"


def _lines(n: int) -> list[str]:
    return [SAFE.format(i=i) for i in range(n)]


# --- unit -------------------------------------------------------------------


@pytest.mark.unit
def test_under_cap_keeps_everything_live() -> None:
    buf = ScrollbackBuffer(max_lines=100)
    for line in _lines(100):
        buf.append(line)
    assert len(buf) == 100
    assert buf.lines() == _lines(100)
    assert buf.spilled() == []
    assert buf.spilled_count == 0


@pytest.mark.unit
def test_over_cap_spills_oldest_first_and_bounds_len(tmp_path: Path) -> None:
    buf = ScrollbackBuffer(max_lines=100, path=tmp_path / "run.jsonl")
    for line in _lines(500):
        buf.append(line)
    assert len(buf) == 100
    assert buf.lines() == _lines(500)[400:]
    assert buf.spilled_count == 400


@pytest.mark.unit
def test_spilled_returns_exact_lines_in_order(tmp_path: Path) -> None:
    buf = ScrollbackBuffer(max_lines=100, path=tmp_path / "run.jsonl")
    for line in _lines(500):
        buf.append(line)
    assert buf.spilled() == _lines(400)
    assert buf.spilled_count == 400


@pytest.mark.unit
def test_all_lines_reconstructs_original_sequence(tmp_path: Path) -> None:
    buf = ScrollbackBuffer(max_lines=100, path=tmp_path / "run.jsonl")
    original = _lines(500)
    for line in original:
        buf.append(line)
    # Strongest assertion: exact order and content of the full history.
    assert buf.all_lines() == original


@pytest.mark.unit
def test_spill_without_store_does_not_crash() -> None:
    """No store configured: overflow is dropped from memory, never spilled.

    Documented behaviour — the live buffer still bounds at ``max_lines`` and
    ``spilled()`` stays empty (nothing was persisted).
    """
    buf = ScrollbackBuffer(max_lines=10)
    for line in _lines(50):
        buf.append(line)
    assert len(buf) == 10
    assert buf.lines() == _lines(50)[40:]
    assert buf.spilled() == []
    assert buf.spilled_count == 0


# --- negative ---------------------------------------------------------------


@pytest.mark.negative
@pytest.mark.parametrize("bad", [0, -1, -100])
def test_non_positive_max_lines_rejected(bad: int) -> None:
    with pytest.raises(ScrollbackError) as excinfo:
        ScrollbackBuffer(max_lines=bad)
    assert isinstance(excinfo.value, HarnessError)
    assert excinfo.value.remediation


# --- integration ------------------------------------------------------------


@pytest.mark.integration
def test_spill_to_real_store_and_reread(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    buf = ScrollbackBuffer(max_lines=100, path=path)
    for line in _lines(500):
        buf.append(line)

    # The artifact file exists on disk and holds the spilled lines in order.
    assert path.exists()
    store = RunArtifactStore(path)
    assert [e["line"] for e in store.entries()] == _lines(400)


@pytest.mark.integration
def test_fresh_buffer_reads_prior_spilled_history(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    first = ScrollbackBuffer(max_lines=100, path=path)
    for line in _lines(500):
        first.append(line)

    # A fresh buffer over the same artifact can read the spilled history.
    second = ScrollbackBuffer(max_lines=100, path=path)
    assert second.spilled() == _lines(400)
    assert second.lines() == []


# --- slow (NIGHTLY, deselected in smoke) ------------------------------------


@pytest.mark.slow
def test_200k_lines_bounded_memory_and_ordered_retrieval(tmp_path: Path) -> None:
    """200k lines: live <= max_lines, full history ordered, RSS growth bounded.

    Uses :class:`_FileSink` (buffered append) rather than ``RunArtifactStore``,
    whose per-append full-file rewrite is O(n) and would make this quadratic.
    """
    total = 200_000
    max_lines = 2000
    path = tmp_path / "soak.jsonl"

    tracemalloc.start()
    with _FileSink(path) as sink:
        buf = ScrollbackBuffer(max_lines=max_lines, spill=sink)
        for i in range(total):
            buf.append(SAFE.format(i=i))
        assert len(buf) <= max_lines
        assert buf.spilled_count == total - max_lines
        history = buf.all_lines()
        assert len(history) == total
        assert history[0] == SAFE.format(i=0)
        assert history[-1] == SAFE.format(i=total - 1)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Bounded growth: the live buffer is capped, so peak stays well under 100 MB.
    assert peak < 100 * 1024 * 1024