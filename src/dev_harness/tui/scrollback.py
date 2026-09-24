"""Bounded scrollback with spill-to-disk (V11 task 7.4, §7.B memory bound).

The on-screen token log (``#canvas-log``, task 7.3) grows without bound if every
streamed line is retained in memory. :class:`ScrollbackBuffer` caps the live
line count at ``max_lines`` and *spills* the oldest lines to the run artifact
file (task 0.15, :class:`~dev_harness.observability.artifacts.RunArtifactStore`),
so history is never lost from disk and remains retrievable in order.

Ordering contract
-----------------
Spilled lines precede live lines. ``all_lines()`` therefore reconstructs the
original append sequence exactly. This holds only while append order is the
insertion order — the buffer is single-threaded by contract (panels write on
the Textual UI thread via the bridge).

No ``tui/ -> engine/`` import: the only dependencies here are the contracts
error taxonomy and the observability artifact store, neither of which imports
``engine/``.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, Self, runtime_checkable

from dev_harness.contracts.errors import ScrollbackError
from dev_harness.observability.artifacts import RunArtifactStore


@runtime_checkable
class ArtifactSink(Protocol):
    """The spill target: an ordered, append-only transcript of dict entries.

    :class:`~dev_harness.observability.artifacts.RunArtifactStore` satisfies this
    protocol. The protocol exists so a lightweight sink can substitute in the
    high-volume soak path (see :class:`_FileSink`) without changing the buffer.
    """

    def append(self, entry: dict[str, Any]) -> None:
        """Append one ordered entry to the transcript."""

    def entries(self) -> list[dict[str, Any]]:
        """Return the transcript entries in append order."""


class _FileSink:
    """Minimal buffered append-only JSONL sink (twin of ``RunArtifactStore``).

    Unlike ``RunArtifactStore.append``, which re-reads and rewrites the whole
    file per call (O(n) per append), this sink keeps one handle open in append
    mode, so spilling ``n`` lines is O(n). Used only for the 200k-line soak,
    which would otherwise be quadratic and unbounded in time. Call :meth:`close`
    (or use as a context manager) to flush and release the handle.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    def append(self, entry: dict[str, Any]) -> None:
        self._fh.write(json.dumps(entry) + "\n")

    def entries(self) -> list[dict[str, Any]]:
        self._fh.flush()
        with self.path.open("r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def close(self) -> None:
        """Flush and close the underlying handle."""
        self._fh.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class ScrollbackBuffer:
    """A fixed-capacity live line buffer that spills overflow to disk.

    :param max_lines: Maximum number of lines kept live (on-screen). Must be
        ``>= 1``.
    :param spill: An artifact sink; if ``None`` and ``path`` is given, a
        :class:`~dev_harness.observability.artifacts.RunArtifactStore` is built
        over ``path``.
    :param path: Convenience path for the spill store.
    :param sink_factory: Injectable factory ``(Path) -> ArtifactSink`` used when
        only ``path`` is supplied (defaults to ``RunArtifactStore``). Tests use a
        real-file sink here; the soak uses :class:`_FileSink` through it.
    """

    def __init__(
        self,
        *,
        max_lines: int = 2000,
        spill: ArtifactSink | None = None,
        path: Path | None = None,
        sink_factory: Callable[[Path], ArtifactSink] | None = None,
    ) -> None:
        if max_lines < 1:
            raise ScrollbackError(
                f"max_lines must be >= 1, got {max_lines}",
                remediation="Configure the scrollback buffer with max_lines >= 1.",
            )
        self._max_lines = max_lines
        self._live: deque[str] = deque()
        if spill is not None:
            self._spill: ArtifactSink | None = spill
        elif path is not None:
            factory = sink_factory or RunArtifactStore
            self._spill = factory(path)
        else:
            self._spill = None
        #: Read back spilled lines; injectable so the soak need not re-read disk.
        self._reader: Callable[[], list[str]] = self._read_spilled
        self._spilled_count = 0

    def _read_spilled(self) -> list[str]:
        if self._spill is None:
            return []
        return [str(entry["line"]) for entry in self._spill.entries()]

    def append(self, line: str) -> None:
        """Append ``line``; spill oldest lines while over ``max_lines``."""
        self._live.append(line)
        # Bounded: each iteration removes one line, so the deque shrinks.
        while len(self._live) > self._max_lines:
            oldest = self._live.popleft()
            self._spill_line(oldest)

    def _spill_line(self, line: str) -> None:
        """Spill one line to the artifact store (no-op if no store configured)."""
        if self._spill is not None:
            self._spill.append({"line": line})
            self._spilled_count += 1

    def lines(self) -> list[str]:
        """The current live lines (``<= max_lines``)."""
        return list(self._live)

    def spilled(self) -> list[str]:
        """All spilled lines, in original order (read back from the store)."""
        return self._reader()

    def all_lines(self) -> list[str]:
        """Full history: spilled then live, in original append order."""
        return self.spilled() + list(self._live)

    @property
    def spilled_count(self) -> int:
        """Number of lines spilled to disk this session."""
        return self._spilled_count

    @property
    def max_lines(self) -> int:
        """The live-line cap."""
        return self._max_lines

    def __len__(self) -> int:
        return len(self._live)
