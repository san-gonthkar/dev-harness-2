"""Checkpoint<->Git binding inside the workspace lock (V11 1.11)."""

from __future__ import annotations

from pathlib import Path

from dev_harness.contracts.state import HarnessState
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver
from dev_harness.storage.workspace_lock import WorkspaceLock
from dev_harness.vcs.git import GitAdapter


class CheckpointBinding:
    """Writes checkpoints whose git_commit_hash equals HEAD at write time."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)
        self.db_path = self.workspace / ".dev-harness" / "state.db"
        self.lock_path = self.workspace / ".dev-harness" / "workspace.lock"

    def put_bound(
        self,
        scope: Scope,
        state: HarnessState,
        *,
        checkpoint_id: str | None = None,
        is_paused: bool = False,
    ) -> str:
        """Write a checkpoint bound to the current HEAD, under the workspace lock."""
        with WorkspaceLock(self.lock_path):
            git = GitAdapter(self.workspace)
            head = git.head_sha()
            saver = SqliteSaver(self.db_path)
            try:
                return saver.put(
                    scope,
                    state,
                    checkpoint_id=checkpoint_id,
                    git_commit_hash=head,
                    is_paused=is_paused,
                )
            finally:
                saver.close()
