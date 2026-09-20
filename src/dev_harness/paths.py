"""Path derivation: canonical workspace, socket, lock, run artifacts (V11 0.10)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from dev_harness.contracts.errors import PathError


@dataclass(frozen=True)
class DerivedPaths:
    """Canonical, deterministic paths for a workspace."""

    workspace: Path
    harness_dir: Path
    state_db: Path
    socket_path: Path
    lock_path: Path
    run_artifacts: Path


def _canonicalize(workspace: str | Path) -> Path:
    """Resolve a workspace path to a canonical absolute form."""
    path = Path(workspace).expanduser()
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
    )


def socket_path_bytes_ok(socket: Path, limit: int = 104) -> bool:
    """AF_UNIX socket paths are limited (typically 104/108 bytes)."""
    return len(str(socket).encode("utf-8")) <= limit
