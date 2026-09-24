"""Checkpoint<->Git binding inside the workspace lock (V11 1.11, 8.21b).

The binding writes checkpoints whose ``git_commit_hash`` equals HEAD at write
time. As of 8.21b it also captures each worker worktree's HEAD + uncommitted
diff so an in-flight (uncommitted) worktree is recoverable field-for-field.

The two columns added by migration 0002 are single TEXT columns, so each holds a
JSON ``worker_id -> value`` map: ``worktree_head`` holds
``{worker_id: <sha>}`` and ``worktree_diff`` holds ``{worker_id: <patch>}``.
This is the simplest shape that satisfies "each worktree's HEAD + diff" without
overloading a single column or adding a 0003 table.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from dev_harness.contracts.state import HarnessState
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver
from dev_harness.storage.workspace_lock import WorkspaceLock
from dev_harness.vcs.git import GitAdapter


def serialize_worktree_state(
    state: Mapping[str, Mapping[str, str]] | None,
) -> tuple[str | None, str | None]:
    """Serialize a ``worker_id -> {head, diff}`` map into the two columns.

    Returns ``(worktree_head, worktree_diff)`` as JSON maps (sorted keys), or
    ``(None, None)`` when no worktrees were captured.
    """
    if not state:
        return None, None
    heads = {wid: snap.get("head", "") for wid, snap in state.items()}
    diffs = {wid: snap.get("diff", "") for wid, snap in state.items()}
    return json.dumps(heads, sort_keys=True), json.dumps(diffs, sort_keys=True)


def deserialize_worktree_state(
    row: Mapping[str, object],
) -> dict[str, dict[str, str]]:
    """Decode the two columns of a checkpoint row back into the state map."""
    heads_raw = row.get("worktree_head")
    diffs_raw = row.get("worktree_diff")
    if not heads_raw and not diffs_raw:
        return {}
    heads = cast("dict[str, str]", json.loads(str(heads_raw))) if heads_raw else {}
    diffs = cast("dict[str, str]", json.loads(str(diffs_raw))) if diffs_raw else {}
    return {
        wid: {"head": heads.get(wid, ""), "diff": diffs.get(wid, "")}
        for wid in sorted(set(heads) | set(diffs))
    }


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
        worktree_state: Mapping[str, Mapping[str, str]] | None = None,
    ) -> str:
        """Write a checkpoint bound to the current HEAD, under the workspace lock.

        ``worktree_state`` is the optional ``worker_id -> {head, diff}`` map from
        :meth:`engine.worker_workspace.WorkerWorkspace.capture_map`; it is
        serialized into the ``worktree_head``/``worktree_diff`` columns.
        """
        worktree_head, worktree_diff = serialize_worktree_state(worktree_state)
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
                    worktree_head=worktree_head,
                    worktree_diff=worktree_diff,
                )
            finally:
                saver.close()
