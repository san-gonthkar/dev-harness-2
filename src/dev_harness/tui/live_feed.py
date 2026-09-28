"""TUI live feed: connect the bridge to the engine's streaming socket (7.7).

``tui/`` must never import ``engine/`` — they communicate only over IPC. This
module is the composition point that lives in ``tui/`` and speaks only the
envelope vocabulary:

* it resolves the engine's **streaming** endpoint (the published discovery
  file, or the derived AF_UNIX path);
* it connects with :class:`~dev_harness.ipc.client.IpcClient`;
* it feeds every received envelope into a :class:`~dev_harness.tui.bridge.Bridge`.

The connection is optional: if no engine is running the TUI still renders, it
simply has no live data. That keeps ``dev-harness`` usable as a viewer.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dev_harness.contracts.events import Envelope
from dev_harness.ipc.discovery import endpoint_is_alive, read_endpoint
from dev_harness.ipc.transport import Endpoint
from dev_harness.paths import derive_paths

if TYPE_CHECKING:
    from dev_harness.tui.bridge import Bridge

#: How long the reader blocks on a socket read before checking for shutdown.
READ_TIMEOUT = 0.2


class LiveFeed:
    """Streams engine envelopes into a TUI bridge.

    :param workspace: the workspace whose engine to attach to.
    :param bridge: the bridge to feed.
    :param endpoint: an explicit endpoint; otherwise discovery is used.
    """

    def __init__(
        self,
        workspace: str | Path,
        bridge: Bridge,
        *,
        endpoint: Endpoint | None = None,
        client_factory: Callable[[Endpoint], Any] | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.paths = derive_paths(self.workspace)
        self.bridge = bridge
        self._endpoint = endpoint
        self._client_factory = client_factory or _default_client
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: Any = None
        #: Envelopes received from the engine.
        self.received = 0
        #: True once the socket connected.
        self.connected = False

    def resolve_endpoint(self) -> Endpoint:
        """The engine's streaming endpoint, or the derived AF_UNIX path."""
        discovered = read_endpoint(self.paths.endpoint_file)
        if discovered is not None and endpoint_is_alive(discovered):
            return discovered
        return Endpoint(kind="unix", address=str(self.paths.socket_path))

    def start(self) -> None:
        """Connect and begin streaming (idempotent)."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="hermes-live-feed", daemon=True
        )
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        """Signal the reader to exit and join it with a bounded timeout."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        self._thread = None
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001, S110 - best-effort close
                pass
            self._client = None

    def _run(self) -> None:
        """Reader loop: connect, then hand every envelope to the bridge."""
        endpoint = self._endpoint or self.resolve_endpoint()
        try:
            client = self._client_factory(endpoint)
        except Exception:  # noqa: BLE001 - no engine is a valid state
            return
        self._client = client
        self.connected = True
        while not self._stop.is_set():
            try:
                env = client.read()
            except Exception:  # noqa: BLE001 - a dead socket ends the feed
                break
            if env is None:
                continue
            self.received += 1
            self.bridge.push(env)


def _default_client(endpoint: Endpoint) -> Any:
    """Build a blocking-read IPC client for the streaming endpoint."""
    from dev_harness.ipc.client import IpcClient

    return _BlockingReader(IpcClient(Path("."), endpoint=endpoint))


class _BlockingReader:
    """Adapts :class:`IpcClient` to a blocking ``read()`` for the feed loop."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._client._connect()

    def read(self) -> Envelope | None:
        """Read one envelope, or ``None`` on a timeout."""

        from dev_harness.ipc.framing import read_frame

        conn = self._client._conn
        if conn is None:
            return None
        try:
            conn.settimeout(READ_TIMEOUT)
            return read_frame(conn)
        except TimeoutError:
            return None
        except (OSError, ValueError):
            raise

    def close(self) -> None:
        self._client.close()
