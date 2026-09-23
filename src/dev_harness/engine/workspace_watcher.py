"""Workspace watcher: FILE_CHANGE and GIT_STATUS_UPDATE producer (V11 5.11).

Two event types had consumers but no producer (plan §2.5): ``FILE_CHANGE``
and ``GIT_STATUS_UPDATE``. This module is that producer. It polls the
workspace for filesystem changes (per-file content fingerprints) and for
git state changes (branch + uncommitted count) and publishes the matching
envelopes through a sink — the daemon's fanout.

Polling (not inotify) is deliberate: the plan's watcher demo only requires
detection within 1s, and polling is portable and testable without platform
hooks. ``scan_once`` is synchronous and deterministic, so tests drive it
directly rather than racing a thread.

Files are fingerprinted by content digest, not ``st_mtime``: filesystem
mtime resolution is coarse (notably on Windows, where rapid writes can
share a timestamp), while the acceptance criterion is that a ``touch`` is
detected. Digesting each watched file per poll is affordable at the demo
scale this protocol runs at; a native inotify backend would replace this
if the watcher ever ran against a large repository.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import (
    Envelope,
    FileChangePayload,
    GitStatusUpdatePayload,
)

ChangeType = Literal["modified", "created", "deleted"]
from dev_harness.vcs.git import GitAdapter

# Detection budget: the 5.B/5.D criteria require a change to be observed
# within 1s, so the default poll interval is well under that.
DEFAULT_POLL_INTERVAL = 0.25

Publish = Callable[[Envelope], None]

# Internal directories that are never treated as watched source.
_EXCLUDED_DIRS = {".git", ".dev-harness", "__pycache__", ".mypy_cache", ".ruff_cache"}

# Fingerprint = (size, content digest). The size is a cheap discriminator;
# the digest catches same-size edits that a coarse mtime would miss.
Fingerprint = tuple[int, str]


class WorkspaceWatcher:
    """Publishes FILE_CHANGE and GIT_STATUS_UPDATE on workspace changes.

    ``publish`` is any ``Envelope`` sink (the daemon passes ``Fanout.publish``).
    ``scan_once`` computes the current state, diffs it against the previous
    scan, emits one envelope per change, and returns the emitted envelopes.
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        publish: Publish | None = None,
        git: GitAdapter | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self._publish = publish
        self._git = git if git is not None else GitAdapter(self.workspace)
        self.poll_interval = poll_interval
        self._seq = 0
        self._fingerprints: dict[str, Fingerprint] = {}
        self._branch: str | None = None
        self._dirty_count: int | None = None
        self._primed = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- introspection -------------------------------------------------------

    @property
    def branch(self) -> str | None:
        """The git branch observed at the last scan, if primed."""
        return self._branch

    @property
    def dirty_count(self) -> int | None:
        """The uncommitted-file count observed at the last scan, if primed."""
        return self._dirty_count

    # -- file scanning -------------------------------------------------------

    def _scan_files(self) -> dict[str, Fingerprint]:
        """Fingerprint every watched file as (size, content digest)."""
        seen: dict[str, Fingerprint] = {}
        if not self.workspace.exists():
            return seen
        for path in self.workspace.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.workspace)
            if any(part in _EXCLUDED_DIRS for part in rel.parts):
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            seen[str(rel)] = (len(data), hashlib.sha256(data).hexdigest())
        return seen

    def _file_changes(
        self, current: dict[str, Fingerprint]
    ) -> list[tuple[str, ChangeType]]:
        """Diff current fingerprints against the previous scan."""
        changes: list[tuple[str, ChangeType]] = []
        for rel, fingerprint in current.items():
            if rel not in self._fingerprints:
                changes.append((rel, "created"))
            elif self._fingerprints[rel] != fingerprint:
                changes.append((rel, "modified"))
        for rel in self._fingerprints:
            if rel not in current:
                changes.append((rel, "deleted"))
        return sorted(changes)

    # -- git scanning --------------------------------------------------------

    def _git_state(self) -> tuple[str, int]:
        """Read (branch, uncommitted_count) from the workspace repo."""
        return self._git.active_branch(), self._git.uncommitted_count()

    # -- emission ------------------------------------------------------------

    def _envelope(
        self, payload: FileChangePayload | GitStatusUpdatePayload
    ) -> Envelope:
        event_type = EventType(payload.type)
        envelope = Envelope(type=event_type, seq=self._seq, payload=payload)
        self._seq += 1
        if self._publish is not None:
            self._publish(envelope)
        return envelope

    def scan_once(self) -> list[Envelope]:
        """Scan once, emit change envelopes, and return them.

        The first call primes the baseline: it records the starting state and
        emits nothing, so watchers do not report the whole tree as ``created``.
        """
        emitted: list[Envelope] = []
        current = self._scan_files()
        branch, dirty_count = self._git_state()

        if self._primed:
            for rel, change_type in self._file_changes(current):
                emitted.append(
                    self._envelope(
                        FileChangePayload(
                            type="FILE_CHANGE", path=rel, change_type=change_type
                        )
                    )
                )
            if branch != self._branch or dirty_count != self._dirty_count:
                emitted.append(
                    self._envelope(
                        GitStatusUpdatePayload(
                            type="GIT_STATUS_UPDATE",
                            branch=branch,
                            dirty_count=dirty_count,
                        )
                    )
                )

        self._fingerprints = current
        self._branch = branch
        self._dirty_count = dirty_count
        self._primed = True
        return emitted

    # -- background loop -----------------------------------------------------

    def start(self) -> None:
        """Begin polling in a background thread."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_interval):
            self.scan_once()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the background poller and join it."""
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=timeout)

    def run_forever(self) -> None:
        """Block, scanning until ``stop`` is requested (foreground mode)."""
        self.scan_once()
        while not self._stop.wait(self.poll_interval):
            self.scan_once()
