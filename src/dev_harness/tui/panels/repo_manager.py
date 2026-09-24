"""Repo-manager panel — workspace tree + git status table (V11 task 7.2).

``#repo-manager`` renders a ``DirectoryTree`` rooted at the workspace path and a
``DataTable`` of git status. It is pure rendering: no engine logic, no
``tui/ -> engine/`` import, and no git dependency at render time — every value
comes from an event payload. Envelopes arrive via a
:class:`~dev_harness.tui.bridge.Bridge` whose handlers run on the Textual UI
thread, so the panel writes directly.

Idempotency contract (7.B): the table has a fixed row set (``branch``, ``dirty``)
plus one row per distinct changed path, keyed by path. Repeated updates use
``update_cell`` and never duplicate rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, DirectoryTree
from textual.widgets.data_table import ColumnKey

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import (
    Envelope,
    FileChangePayload,
    GitStatusUpdatePayload,
)

if TYPE_CHECKING:
    from dev_harness.tui.bridge import Bridge

#: DirectoryTree widget id for the workspace tree.
REPO_TREE_ID = "#repo-tree"
#: DataTable widget id for the git status rows.
GIT_STATUS_ID = "#git-status"

#: Fixed row keys for the git status cells.
_BRANCH_ROW = "branch"
_DIRTY_ROW = "dirty"
#: Column keys for the two-column status table.
_FIELD_COL = "field"
_VALUE_COL = "value"


class RepoManager(Vertical):
    """Workspace ``DirectoryTree`` + git status ``DataTable`` for ``#repo-manager``."""

    DEFAULT_CSS = """
    RepoManager {
        layout: vertical;
    }
    RepoManager > #repo-tree {
        height: 1fr;
        width: 1fr;
    }
    RepoManager > #git-status {
        height: auto;
        max-height: 8;
        width: 1fr;
    }
    """

    def __init__(
        self,
        *,
        path: str | Path = ".",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._path = Path(path)
        self._tree = DirectoryTree(str(self._path), id="repo-tree")
        self._table: DataTable[str] = DataTable(id="git-status")
        #: Column key for the value column; created on mount (needs an active app).
        self._value_col: ColumnKey | None = None
        #: Current branch name; empty until the first GIT_STATUS_UPDATE.
        self._branch = ""
        #: Current dirty-file count; 0 until the first GIT_STATUS_UPDATE.
        self._dirty_count = 0
        #: path -> change_type, last-write-wins per path.
        self._changed_paths: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        """Yield the workspace tree and the git status table as vertical children."""
        yield self._tree
        yield self._table

    def on_mount(self) -> None:
        """Create the fixed table columns and the git status rows."""
        self._ensure_rows()

    def on_file_change(self, payload: FileChangePayload) -> None:
        """Record a file change; refresh its row in place (never duplicates)."""
        self._changed_paths[payload.path] = payload.change_type
        if not self.is_mounted:
            return
        self._ensure_rows()
        if self._value_col is None:
            return
        if payload.path in self._table.rows:
            self._table.update_cell(payload.path, self._value_col, payload.change_type)
        else:
            self._table.add_row(payload.path, payload.change_type, key=payload.path)

    def on_git_status(self, payload: GitStatusUpdatePayload) -> None:
        """Update the branch and dirty-count cells from a ``GIT_STATUS_UPDATE``."""
        self._branch = payload.branch
        self._dirty_count = payload.dirty_count
        if not self.is_mounted:
            return
        self._ensure_rows()
        if self._value_col is None:
            return
        self._table.update_cell(_BRANCH_ROW, self._value_col, self._branch)
        self._table.update_cell(_DIRTY_ROW, self._value_col, str(self._dirty_count))

    def bind(self, bridge: Bridge) -> None:
        """Register this panel's handlers with a bridge (callbacks run on the UI thread)."""
        bridge.on(EventType.FILE_CHANGE, self._handle_file_change)
        bridge.on(EventType.GIT_STATUS_UPDATE, self._handle_git_status)

    def _handle_file_change(self, env: Envelope) -> None:
        payload = env.payload
        if isinstance(payload, FileChangePayload):
            self.on_file_change(payload)

    def _handle_git_status(self, env: Envelope) -> None:
        payload = env.payload
        if isinstance(payload, GitStatusUpdatePayload):
            self.on_git_status(payload)

    def _ensure_rows(self) -> None:
        """Create the columns and fixed git status rows once (idempotent)."""
        if self._value_col is None:
            self._value_col = self._table.add_columns(_FIELD_COL, _VALUE_COL)[1]
        if _BRANCH_ROW not in self._table.rows:
            self._table.add_row(_BRANCH_ROW, self._branch, key=_BRANCH_ROW)
        if _DIRTY_ROW not in self._table.rows:
            self._table.add_row(_DIRTY_ROW, str(self._dirty_count), key=_DIRTY_ROW)

    @property
    def branch(self) -> str:
        """The branch name from the last ``GIT_STATUS_UPDATE`` (empty before any)."""
        return self._branch

    @property
    def dirty_count(self) -> int:
        """The dirty-file count from the last ``GIT_STATUS_UPDATE`` (0 before any)."""
        return self._dirty_count

    @property
    def changed_paths(self) -> dict[str, str]:
        """Recorded ``FILE_CHANGE`` paths mapped to their latest change type."""
        return dict(self._changed_paths)

    @property
    def displayed_branch(self) -> str:
        """The branch cell as rendered in the table (empty when unmounted)."""
        if self._value_col is None or _BRANCH_ROW not in self._table.rows:
            return ""
        return str(self._table.get_cell(_BRANCH_ROW, self._value_col))

    @property
    def displayed_dirty_count(self) -> str:
        """The dirty-count cell as rendered in the table (empty when unmounted)."""
        if self._value_col is None or _DIRTY_ROW not in self._table.rows:
            return ""
        return str(self._table.get_cell(_DIRTY_ROW, self._value_col))

    @property
    def row_count(self) -> int:
        """Number of rows currently in the status table."""
        return len(self._table.rows)
