"""Path derivation: canonical workspace, socket, lock, run artifacts (V11 0.10)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from dev_harness.contracts.errors import PathError


@dataclass(frozen=True)
class DerivedPaths:
    """Canonical, deterministic paths for a workspace.

    The engine daemon owns **two** endpoints (ADR-0002, V11 5.1/5.3):

    * ``socket_path`` — the streaming socket carrying the envelope vocabulary
      (state broadcast, fan-out to TUI clients).
    * ``control_socket_path`` — the control socket carrying the command
      vocabulary (START_SESSION, ATTACH, DETACH, STATUS, SHUTDOWN).

    Each has a companion ``*.endpoint`` file, written only when the daemon
    binds TCP (native Windows) so clients can discover the dynamic port.
    """

    workspace: Path
    harness_dir: Path
    state_db: Path
    socket_path: Path
    lock_path: Path
    run_artifacts: Path
    #: Where the daemon publishes its bound streaming endpoint (TCP port).
    endpoint_file: Path
    #: The control socket carrying the command vocabulary.
    control_socket_path: Path
    #: Where the daemon publishes its bound control endpoint (TCP port).
    control_endpoint_file: Path


def _canonicalize(workspace: str | Path) -> Path:
    """Resolve a workspace path to a canonical absolute form.

    Raises :class:`PathError` for an empty/blank workspace or one that cannot be
    resolved to an absolute path.
    """
    raw = str(workspace).strip()
    if not raw:
        raise PathError(
            "workspace path is empty",
            remediation="Provide a canonical, absolute workspace path.",
        )
    path = Path(raw).expanduser()
    # Resolve symlinks (must exist). Trailing slashes are normalized by resolve.
    resolved = path.resolve()
    if not resolved.is_absolute():
        raise PathError(
            f"workspace path is not absolute: {resolved}",
            remediation="Provide an absolute workspace path.",
        )
    return resolved


def _namespace_id(workspace: Path) -> str:
    """A stable short id derived from the canonical path."""
    return hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:16]


def derive_paths(workspace: str | Path) -> DerivedPaths:
    """Derive all harness paths from a canonical workspace path."""
    ws = _canonicalize(workspace)
    harness = ws / ".dev-harness"
    ns = _namespace_id(ws)
    return DerivedPaths(
        workspace=ws,
        harness_dir=harness,
        state_db=harness / "state.db",
        socket_path=harness / f"harness-{ns}.sock",
        lock_path=harness / "workspace.lock",
        run_artifacts=harness / "runs",
        endpoint_file=harness / "engine.endpoint",
        control_socket_path=harness / f"harness-{ns}.ctl.sock",
        control_endpoint_file=harness / "engine.ctl.endpoint",
    )


def socket_path_bytes_ok(socket: Path, limit: int = 104) -> bool:
    """AF_UNIX socket paths are limited (typically 104/108 bytes)."""
    return len(str(socket).encode("utf-8")) <= limit
