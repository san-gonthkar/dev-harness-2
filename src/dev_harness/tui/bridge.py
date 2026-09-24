"""IPC -> UI bridge (V11 task 7.7).

A single daemon thread reads ``Envelope``s from an injectable source (a
``BackpressureQueue`` or a ``() -> Envelope | None`` callable) and marshals
each one onto the Textual UI thread via ``App.call_from_thread``. Envelopes are
applied strictly in arrival order, so a ``SNAPSHOT`` that arrives first is
applied before any delta.

``tui/`` never imports ``engine/``: the bridge consumes envelopes and does not
know who produced them. ``ipc.client.IpcClient`` is POSIX-only, so the source is
injected rather than constructed here.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from textual._context import NoActiveAppError

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope, SnapshotPayload
from dev_harness.contracts.state import HarnessState
from dev_harness.ipc.queue import BackpressureQueue

if TYPE_CHECKING:
    from dev_harness.tui.app import HermesApp

#: How long the reader thread idles between polls when the source is empty.
DEFAULT_POLL_INTERVAL = 0.01
#: Upper bound on ``stop()``'s join — never an unbounded join.
DEFAULT_STOP_TIMEOUT = 2.0

#: A pull-based envelope source; returns ``None`` when idle.
EnvelopeSource = Callable[[], Envelope | None]

#: A handler invoked on the UI thread for one event type.
EnvelopeHandler = Callable[[Envelope], None]


class Bridge:
    """Marshals IPC envelopes onto the Textual UI thread, in arrival order."""

    def __init__(
        self,
        app: HermesApp,
        *,
        source: EnvelopeSource | BackpressureQueue | None = None,
        queue: BackpressureQueue | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        if isinstance(source, BackpressureQueue):
            if queue is None:
                queue = source
            source = None
        self.app = app
        self.poll_interval = poll_interval
        #: Last SNAPSHOT state; ``None`` until the first SNAPSHOT is applied.
        self.state: HarnessState | None = None
        #: True once a SNAPSHOT has been applied (deltas may precede it).
        self.snapshot_seen = False
        #: Envelopes applied on the UI thread.
        self.applied = 0
        #: Applied envelopes that are not ``AGENT_TOKEN_STREAM``.
        self.control_applied = 0
        self._queue = queue
        self._dropped = 0
        self._handlers: dict[EventType, list[EnvelopeHandler]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if source is None:
            if queue is None:
                raise ValueError("Bridge requires a source callable or a BackpressureQueue")
            self._pull: EnvelopeSource = queue.get
        else:
            self._pull = source

    @property
    def dropped(self) -> int:
        """Envelopes lost: queue backpressure drops plus unmarshalable frames."""
        queued = self._queue.dropped_frames if self._queue is not None else 0
        return self._dropped + queued

    def on(self, event_type: EventType, callback: EnvelopeHandler) -> None:
        """Register a handler invoked on the UI thread for ``event_type``."""
        self._handlers.setdefault(event_type, []).append(callback)

    def start(self) -> None:
        """Spawn the single daemon reader thread (idempotent)."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hermes-bridge", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = DEFAULT_STOP_TIMEOUT) -> None:
        """Signal the reader thread to exit and join it with a bounded timeout."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        self._thread = None

    def _run(self) -> None:
        """Reader loop: bounded by ``self._stop`` — never an unbounded drain."""
        while not self._stop.is_set():
            env = self._pull()
            if env is None:
                self._stop.wait(self.poll_interval)
                continue
            try:
                self.app.call_from_thread(self._apply, env)
            except NoActiveAppError:
                # The app is gone; the frame cannot be marshalled. Record and exit.
                self._dropped += 1
                return

    def _apply(self, env: Envelope) -> None:
        """Apply one envelope on the UI thread, preserving arrival order."""
        if env.type is EventType.SNAPSHOT and isinstance(env.payload, SnapshotPayload):
            self.state = env.payload.state
            self.snapshot_seen = True
        self.applied += 1
        if env.type is not EventType.AGENT_TOKEN_STREAM:
            self.control_applied += 1
        for callback in self._handlers.get(env.type, []):
            callback(env)