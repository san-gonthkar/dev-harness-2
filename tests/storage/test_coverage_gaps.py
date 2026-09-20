"""Additional coverage for migrate CLI, retention edges, lock context manager."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dev_harness.contracts.state import HarnessState
from dev_harness.storage import migrate
from dev_harness.storage.retention import prune_and_vacuum
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver
from dev_harness.storage.workspace_lock import WorkspaceLock

pytestmark = pytest.mark.unit


def _state(project: str, thread: str, raw: str = "x") -> HarnessState:
    return HarnessState(
        project_id=project, workspace_path="/w", thread_id=thread, raw_input=raw
    )


class TestMigrateCli:
    def test_missing_workspace_returns_2(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert migrate.main() == 2
        assert "usage" in capsys.readouterr().err

    def test_up_dispatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (tmp_path / ".dev-harness").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(
            sys, "argv", ["migrate", "--workspace", str(tmp_path), "--up"]
        )
        assert migrate.main() == 0
        assert "applied" in capsys.readouterr().out

    def test_down_dispatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (tmp_path / ".dev-harness").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(
            sys, "argv", ["migrate", "--workspace", str(tmp_path), "--down"]
        )
        assert migrate.main() == 0
        assert "rolled back" in capsys.readouterr().out

    def test_neither_up_nor_down(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["migrate", "--workspace", str(tmp_path)])
        assert migrate.main() == 2
        assert "specify" in capsys.readouterr().err


class TestRetentionEdges:
    def test_fewer_than_keep_prunes_nothing(self, tmp_path: Path) -> None:
        saver = SqliteSaver(tmp_path / "db.sqlite")
        for i in range(3):
            saver.put(
                Scope("p1", "t1"),
                _state("p1", "t1", f"v{i}"),
                created_at=i,
                checkpoint_id=f"c{i}",
            )
        saver.close()
        deleted = prune_and_vacuum(tmp_path / "db.sqlite", Scope("p1", "t1"), keep=5)
        assert deleted == 0

    def test_keep_zero_prunes_all_non_seals(self, tmp_path: Path) -> None:
        saver = SqliteSaver(tmp_path / "db.sqlite")
        for i in range(3):
            saver.put(
                Scope("p1", "t1"),
                _state("p1", "t1", f"v{i}"),
                created_at=i,
                checkpoint_id=f"c{i}",
            )
        saver.close()
        deleted = prune_and_vacuum(tmp_path / "db.sqlite", Scope("p1", "t1"), keep=0)
        assert deleted == 3

    def test_other_scope_untouched(self, tmp_path: Path) -> None:
        saver = SqliteSaver(tmp_path / "db.sqlite")
        for i in range(10):
            saver.put(
                Scope("p1", "t1"),
                _state("p1", "t1", f"v{i}"),
                created_at=i,
                checkpoint_id=f"c{i}",
            )
        saver.put(
            Scope("p2", "t2"),
            _state("p2", "t2", "keep"),
            created_at=0,
            checkpoint_id="other",
        )
        saver.close()
        prune_and_vacuum(tmp_path / "db.sqlite", Scope("p1", "t1"), keep=5)
        saver = SqliteSaver(tmp_path / "db.sqlite")
        rows = saver.list(Scope("p2", "t2"), limit=100)
        assert len(rows) == 1
        saver.close()


class TestLockContextManager:
    def test_context_manager_acquire_release(self, tmp_path: Path) -> None:
        with WorkspaceLock(tmp_path / "ws.lock", timeout=1.0) as lock:
            assert lock.lock_path.exists()
        assert not lock.lock_path.exists()

    def test_release_without_acquire_is_noop(self, tmp_path: Path) -> None:
        lock = WorkspaceLock(tmp_path / "ws.lock", timeout=1.0)
        lock.release()  # should not raise
        assert not lock.lock_path.exists()
