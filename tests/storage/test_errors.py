"""Resource-exhaustion translation tests (V11 9.8).

ENOSPC and SQLITE_BUSY must map to HarnessError subclasses with actionable
remediation, and the mapping must be total so no raw sqlite3/OSError traceback
reaches the TUI. Failures are simulated by injecting raising callables - no
disk is filled and no real lock contention is created.
"""

from __future__ import annotations

import errno
import sqlite3

import pytest

from dev_harness.contracts.errors import (
    DatabaseBusyError,
    DiskFullError,
    HarnessError,
    StorageError,
)
from dev_harness.storage.errors import (
    translate_storage_error,
    translate_storage_errors,
)


def _enospc() -> OSError:
    return OSError(errno.ENOSPC, "No space left on device")


def _busy() -> sqlite3.OperationalError:
    return sqlite3.OperationalError("database is locked")


# --- (a) ENOSPC -------------------------------------------------------------


@pytest.mark.unit
def test_enospc_maps_to_disk_full_error() -> None:
    err = translate_storage_error(_enospc())
    assert isinstance(err, DiskFullError)
    assert isinstance(err, StorageError)
    assert err.remediation


@pytest.mark.unit
def test_enospc_remediation_is_actionable() -> None:
    err = translate_storage_error(_enospc())
    assert "disk space" in err.remediation.lower()


# --- (b) SQLITE_BUSY --------------------------------------------------------


@pytest.mark.unit
def test_sqlite_busy_maps_to_database_busy_error() -> None:
    err = translate_storage_error(_busy())
    assert isinstance(err, DatabaseBusyError)
    assert isinstance(err, StorageError)
    assert err.remediation


@pytest.mark.unit
def test_sqlite_busy_by_error_code() -> None:
    exc = sqlite3.OperationalError("some other message")
    exc.sqlite_errorcode = 5  # SQLITE_BUSY
    err = translate_storage_error(exc)
    assert isinstance(err, DatabaseBusyError)


@pytest.mark.unit
def test_sqlite_locked_by_error_code() -> None:
    exc = sqlite3.OperationalError("some other message")
    exc.sqlite_errorcode = 6  # SQLITE_LOCKED
    assert isinstance(translate_storage_error(exc), DatabaseBusyError)


# --- (c) totality: no raw sqlite3/OSError escapes ---------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        _enospc(),
        _busy(),
        sqlite3.OperationalError("no such table: checkpoints"),
        sqlite3.DatabaseError("file is not a database"),
        OSError(errno.EACCES, "Permission denied"),
        OSError(errno.EROFS, "Read-only file system"),
        ValueError("not a storage error at all"),
    ],
)
def test_translation_is_total(exc: BaseException) -> None:
    err = translate_storage_error(exc)
    assert isinstance(err, HarnessError)
    assert err.remediation
    assert not isinstance(err, (sqlite3.Error, OSError))


@pytest.mark.unit
def test_harness_error_passes_through_unchanged() -> None:
    original = DiskFullError("already translated")
    assert translate_storage_error(original) is original


@pytest.mark.unit
def test_context_manager_translates_enospc() -> None:
    with pytest.raises(DiskFullError) as info, translate_storage_errors():
        raise _enospc()
    assert info.value.remediation


@pytest.mark.unit
def test_context_manager_translates_busy() -> None:
    with pytest.raises(DatabaseBusyError), translate_storage_errors():
        raise _busy()


@pytest.mark.unit
def test_context_manager_preserves_harness_error() -> None:
    original = StorageError("boom")
    with pytest.raises(StorageError) as info, translate_storage_errors():
        raise original
    assert info.value is original


@pytest.mark.unit
def test_context_manager_passes_through_success() -> None:
    with translate_storage_errors():
        pass


@pytest.mark.negative
def test_context_manager_does_not_swallow_unrelated_errors() -> None:
    with pytest.raises(ValueError), translate_storage_errors():
        raise ValueError("programmer error")
