"""Branch-coverage completion tests for storage (V11 1.x)."""

from __future__ import annotations

import builtins
import runpy
import sqlite3
import sys
from pathlib import Path
from typing import Any

import portalocker
import pytest

from dev_harness.contracts.errors import LockTimeout
from dev_harness.storage import cli, migrate
from dev_harness.storage.connection import connect
from dev_harness.storage.retention import vacuum_with_retry
from dev_harness.storage.workspace_lock import WorkspaceLock, _pid_alive

pytestmark = pytest.mark.unit


class TestCliBranches:
    def test_main_unknown_command_returns_2(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeArgs:
            command = "bogus"

        class FakeParser:
            def parse_args(self) -> FakeArgs:
                return FakeArgs()

        monkeypatch.setattr(cli, "build_parser", lambda: FakeParser())
        assert cli.main() == 2

    def test_module_entry_raises_system_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.support.workspace import make_workspace

        ws = make_workspace(tmp_path)
        (ws / "dirty.txt").write_text("x", encoding="utf-8")
        monkeypatch.setattr(
            sys, "argv", ["cli", "restore", "--workspace", str(ws), "--to", "HEAD"]
        )
        with pytest.raises(SystemExit) as ei:
            runpy.run_module("dev_harness.storage.cli", run_name="__main__")
        assert ei.value.code == 2


class TestMigrateBranches:
    def test_down_with_target(self, tmp_path: Path) -> None:
        db = tmp_path / "state.db"
        migrate.migrate_up(db)
        rolled = migrate.migrate_down(db, target="0000")
        assert rolled == ["0002", "0001"]

    def test_module_entry_returns_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["migrate"])
        with pytest.raises(SystemExit) as ei:
            runpy.run_module("dev_harness.storage.migrate", run_name="__main__")
        assert ei.value.code == 2


class TestRetentionBranches:
    def test_vacuum_exhausted_retries_no_raise(self, tmp_path: Path) -> None:
        conn = connect(tmp_path / "db.sqlite")
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()

        class BusyAlways:
            def __init__(self, inner: sqlite3.Connection) -> None:
                self._inner = inner

            def execute(self, sql: str, params: Any = None) -> object:
                if "VACUUM" in str(sql):
                    raise sqlite3.OperationalError("database is locked")
                if params is None:
                    return self._inner.execute(sql)
                return self._inner.execute(sql, params)

            def commit(self) -> None:
                self._inner.commit()

        vacuum_with_retry(BusyAlways(conn), attempts=3)  # type: ignore[arg-type]
        conn.close()

    def test_vacuum_non_locked_error_raises(self, tmp_path: Path) -> None:
        conn = connect(tmp_path / "db.sqlite")

        class BadVacuum:
            def __init__(self, inner: sqlite3.Connection) -> None:
                self._inner = inner

            def execute(self, sql: str, params: Any = None) -> object:
                if "VACUUM" in str(sql):
                    raise sqlite3.OperationalError("no such table: xyz")
                if params is None:
                    return self._inner.execute(sql)
                return self._inner.execute(sql, params)

            def commit(self) -> None:
                self._inner.commit()

        with pytest.raises(sqlite3.OperationalError):
            vacuum_with_retry(BadVacuum(conn), attempts=3)  # type: ignore[arg-type]
        conn.close()


class TestLockBranches:
    def test_write_owner_without_handle(self, tmp_path: Path) -> None:
        lock = WorkspaceLock(tmp_path / "ws.lock", timeout=1.0)
        lock._write_owner()
        assert lock.lock_path.exists()

    def test_open_failure_then_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """open() raising LockException leaves _fh None; loop until LockTimeout."""
        lock = WorkspaceLock(tmp_path / "ws.lock", timeout=0.3)
        real_open = builtins.open

        def fake_open(*args: object, **kwargs: object) -> object:
            raise portalocker.exceptions.LockException("simulated open failure")

        monkeypatch.setattr(builtins, "open", fake_open)
        with pytest.raises(LockTimeout):
            lock.acquire()
        monkeypatch.setattr(builtins, "open", real_open)

    def test_reclaim_stale_continue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LockException + dead PID in file -> reclaim -> continue -> acquire."""
        lock_path = tmp_path / "ws.lock"
        lock_path.write_text("999999:somehost", encoding="utf-8")
        lock = WorkspaceLock(lock_path, timeout=1.0)
        real_lock = portalocker.lock
        calls = {"n": 0}

        def fake_lock(fh: object, flags: int) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise portalocker.exceptions.LockException("locked")
            real_lock(fh, flags)  # type: ignore[arg-type]

        monkeypatch.setattr(portalocker, "lock", fake_lock)
        lock.acquire()
        assert lock.lock_path.exists()
        lock.release()

    def test_reclaim_stale_live_pid_returns_false(self, tmp_path: Path) -> None:
        lock = WorkspaceLock(tmp_path / "ws.lock", timeout=1.0)
        lock.acquire()
        assert lock._reclaim_stale() is False
        lock.release()

    def test_pid_alive_zero(self) -> None:
        assert _pid_alive(0) is False
        assert _pid_alive(-5) is False
