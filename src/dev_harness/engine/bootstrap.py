"""Daemon autostart from CLI with handshake and version check (V11 5.6).

The bootstrap layer is what a CLI client uses to talk to the engine: it
connects to the workspace-scoped socket, performs a STATUS handshake, and
verifies the daemon's version matches the client's. If no daemon is
running it spawns one (``dev-harness-engine`` subprocess) and retries the
handshake until the socket appears.

The command/response vocabulary lives in ``engine/commands.py``; this
module only owns the client side of that conversation plus process
spawning. On platforms without AF_UNIX the transport raises
UnsupportedPlatformError (the .ps1 verify stub documents the limit).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dev_harness import __version__
from dev_harness.contracts.errors import EngineVersionMismatch, HarnessError
from dev_harness.engine.commands import (
    Command,
    CommandResponse,
    StatusCommand,
    StatusResponse,
    encode_command,
    read_response_frame,
)
from dev_harness.ipc.discovery import endpoint_is_alive, read_endpoint
from dev_harness.ipc.transport import (
    Endpoint,
    TransportError,
)
from dev_harness.ipc.transport import connect as transport_connect
from dev_harness.paths import derive_paths

HANDSHAKE_TIMEOUT = 3.0  # 5.B: handshake completes in under 3s
HANDSHAKE_RETRY_DELAY = 0.05


class EngineUnreachableError(HarnessError):
    """The engine daemon could not be reached for a handshake."""

    remediation = "Start the engine daemon (dev-harness-engine) and retry."


class CommandClient:
    """A blocking command/response client for the engine endpoint.

    Unlike IpcClient (envelope vocabulary), this client speaks the command
    frame protocol from ``engine/commands.py`` — the same framing the
    daemon's command handler decodes.
    """

    def __init__(
        self,
        socket_path: str | Path,
        *,
        socket_factory: Callable[[int, int], Any] | None = None,
        connect_timeout: float = HANDSHAKE_TIMEOUT,
        endpoint: Endpoint | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self._socket_factory = socket_factory
        self.connect_timeout = connect_timeout
        # AF_UNIX is the default; TCP is opt-in via an explicit endpoint.
        self.endpoint = endpoint or Endpoint(kind="unix", address=str(self.socket_path))
        self._conn: Any = None

    def _connect(self) -> Any:
        """Connect to the engine endpoint, raising EngineUnreachableError."""
        try:
            conn = transport_connect(
                self.endpoint,
                self.connect_timeout,
                socket_factory=self._socket_factory,
            )
            self._conn = conn
            return conn
        except (OSError, TransportError) as exc:
            raise EngineUnreachableError(
                f"cannot reach engine at {self.endpoint}",
                remediation="Start the engine daemon (dev-harness-engine) and retry.",
            ) from exc

    def request(self, command: Command) -> CommandResponse:
        """Send a command and read the typed response."""
        if self._conn is None:
            self._connect()
        try:
            self._conn.sendall(encode_command(command))
            return read_response_frame(self._conn)
        except (OSError, ValueError) as exc:
            self._conn = None
            raise EngineUnreachableError(
                f"engine command {command.command} failed",
                remediation="Start the engine daemon (dev-harness-engine) and retry.",
            ) from exc

    def close(self) -> None:
        """Close the connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None


class EngineBootstrap:
    """Autostart + handshake + version check for the engine daemon."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        client_factory: Callable[[Path], CommandClient] | None = None,
        spawn: Callable[[Path], subprocess.Popen[Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.workspace = Path(workspace)
        self.paths = derive_paths(self.workspace)
        self.socket_path = self.paths.socket_path
        self.control_socket_path = self.paths.control_socket_path
        self._client_factory = client_factory or (lambda p: CommandClient(p))
        self._spawn = spawn or self._spawn_daemon
        self._clock = clock

    def resolve_endpoint(self) -> Endpoint:
        """The **control** endpoint to connect to.

        The command vocabulary lives on the control socket (ADR-0002). Prefers
        the daemon-published control endpoint file (written when the daemon
        binds TCP); falls back to the AF_UNIX control path.

        A published endpoint that nothing is listening on is discarded: a
        daemon killed hard never cleans up, so its file goes stale and would
        otherwise strand clients on a dead port.
        """
        path = self.paths.control_endpoint_file
        discovered = read_endpoint(path)
        if discovered is not None and endpoint_is_alive(discovered):
            return discovered
        return Endpoint(kind="unix", address=str(self.control_socket_path))

    def _is_running(self) -> bool:
        """True when a daemon is actually listening.

        A stale published file must not count as running, or ``ensure_daemon``
        would skip the spawn and then fail to connect.
        """
        if self.control_socket_path.exists():
            return True
        discovered = read_endpoint(self.paths.control_endpoint_file)
        return discovered is not None and endpoint_is_alive(discovered)

    # -- handshake -----------------------------------------------------------

    def handshake(self, *, timeout: float = HANDSHAKE_TIMEOUT) -> StatusResponse:
        """Connect and verify the daemon version.

        Raises EngineVersionMismatch when the daemon's version differs from
        the client's; EngineUnreachableError when the socket never appears.
        """
        deadline = self._clock() + timeout
        last_error: EngineUnreachableError | None = None
        while True:
            try:
                endpoint = self.resolve_endpoint()
                client = self._client_factory(self.control_socket_path)
                client.endpoint = endpoint
                try:
                    response = client.request(
                        StatusCommand(workspace=str(self.workspace))
                    )
                finally:
                    client.close()
                if not isinstance(response, StatusResponse):
                    raise EngineUnreachableError(
                        f"unexpected handshake response {type(response).__name__}",
                        remediation="Check the engine daemon log and retry.",
                    )
                if response.version and response.version != __version__:
                    raise EngineVersionMismatch(
                        f"engine daemon version {response.version} != client {__version__}",
                        remediation=(
                            "Reinstall the matching dev-harness version on both sides."
                        ),
                    )
                return response
            except EngineUnreachableError as exc:
                last_error = exc
                if self._clock() >= deadline:
                    break
                time.sleep(HANDSHAKE_RETRY_DELAY)
        assert last_error is not None
        raise last_error

    # -- autostart -----------------------------------------------------------

    def ensure_daemon(self, *, timeout: float = HANDSHAKE_TIMEOUT) -> StatusResponse:
        """Return a handshaken daemon, spawning one if none is listening."""
        if not self._is_running():
            self._spawn(self.workspace)
        return self.handshake(timeout=timeout)

    @staticmethod
    def _spawn_daemon(workspace: Path) -> subprocess.Popen[Any]:
        """Spawn ``dev-harness-engine`` detached from the caller."""
        env = dict(os.environ)
        env["DEV_HARNESS_WORKSPACE"] = str(workspace)
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "dev_harness.engine.daemon",
                "--workspace",
                str(workspace),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ensure the daemon is running and handshake."""
    import argparse

    parser = argparse.ArgumentParser(prog="dev-harness-engine")
    parser.add_argument("--workspace", default=".", help="Workspace directory")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Ensure the daemon is running and report its status",
    )
    args = parser.parse_args(argv)

    bootstrap = EngineBootstrap(args.workspace)
    try:
        response = bootstrap.ensure_daemon()
    except HarnessError as exc:
        print(f"engine unavailable: {exc}", file=sys.stderr)
        return 1
    if args.self_check:
        print(
            f"engine ok: thread_id={response.thread_id} "
            f"state={response.state.value} version={response.version}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
