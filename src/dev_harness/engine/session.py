"""Session manager: id generation and registry (V11 5.2).

One active session per workspace. Session ids are sortable and unique:
``{unix_ns}-{ns16}`` ? the nanosecond prefix is monotonic within the
process (strictly increasing), the random suffix adds cross-process safety.
"""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from dev_harness.contracts.state import HarnessState

_id_lock = threading.Lock()
_last_ns = 0


def new_thread_id() -> str:
    """Generate a unique, sortable session id.

    Format: ``{unix_ns}-{ns16}``. The nanosecond prefix is monotonic
    (strictly increasing within this process), so ids sort in generation
    order; the random suffix guarantees uniqueness across processes.
    """
    global _last_ns
    with _id_lock:
        ns = time.time_ns()
        if ns <= _last_ns:
            ns = _last_ns + 1
        _last_ns = ns
        return f"{ns}-{uuid.uuid4().hex[:16]}"


def project_id_for(workspace: str) -> str:
    """Derive a stable project id from the workspace path.

    Uses sha256 (not ``hash()``) so the id is deterministic across runs.
    """
    digest = hashlib.sha256(workspace.encode("utf-8")).hexdigest()
    return f"ws-{digest[:16]}"


@dataclass
class Session:
    """A live engine session bound to one workspace."""

    thread_id: str
    project_id: str
    workspace_path: str
    state: HarnessState = field(init=False)
    created_at: int = field(default_factory=lambda: int(time.time()))

    def __post_init__(self) -> None:
        self.state = HarnessState(
            project_id=self.project_id,
            workspace_path=self.workspace_path,
            thread_id=self.thread_id,
        )


class SessionManager:
    """Registry of active sessions; one per workspace."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    def new_session(self, workspace: str | Path) -> Session:
        """Create a session for the workspace, or raise SessionExistsError.

        The error carries the existing thread_id so callers can attach.
        """
        workspace = str(Path(workspace).resolve())
        with self._lock:
            existing = self._sessions.get(workspace)
            if existing is not None:
                from dev_harness.contracts.errors import SessionExistsError

                raise SessionExistsError(
                    f"Session already exists for workspace {workspace} "
                    f"(thread_id={existing.thread_id})"
                )
            session = Session(
                thread_id=new_thread_id(),
                project_id=project_id_for(workspace),
                workspace_path=workspace,
            )
            self._sessions[workspace] = session
            return session

    def get(self, workspace: str | Path) -> Session | None:
        """Return the active session for the workspace, if any."""
        workspace = str(Path(workspace).resolve())
        with self._lock:
            return self._sessions.get(workspace)

    def remove(self, workspace: str | Path) -> Session | None:
        """Remove and return the session for the workspace, if any."""
        workspace = str(Path(workspace).resolve())
        with self._lock:
            return self._sessions.pop(workspace, None)

    def list_all(self) -> list[Session]:
        """Return all active sessions (stable order by workspace)."""
        with self._lock:
            return sorted(self._sessions.values(), key=lambda s: s.workspace_path)

    def count(self) -> int:
        """Number of active sessions."""
        with self._lock:
            return len(self._sessions)
