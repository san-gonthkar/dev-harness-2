"""Resource-exhaustion translation for the storage layer (V11 9.8).

Raw ``sqlite3`` and ``OSError`` exceptions must never reach the TUI: every
storage failure is translated into a canonical :class:`HarnessError` subclass
carrying an actionable ``remediation``. :func:`translate_storage_error` is
*total* for the two resource-exhaustion classes the plan names - ENOSPC
(``OSError`` errno 28) and SQLITE_BUSY (``sqlite3.OperationalError``
"database is locked") - and never re-raises a raw ``sqlite3``/``OSError``.
"""

from __future__ import annotations

import errno
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from dev_harness.contracts.errors import (
    DatabaseBusyError,
    DiskFullError,
    HarnessError,
    StorageError,
)

# SQLITE_BUSY = 5, SQLITE_LOCKED = 6 (sqlite3.h). Python 3.11+ exposes the
# numeric code on the exception; the message check is the portable fallback.
_SQLITE_BUSY_CODES = frozenset({5, 6})
_BUSY_MESSAGES = ("database is locked", "database table is locked")


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if code in _SQLITE_BUSY_CODES:
        return True
    message = str(exc).lower()
    return any(fragment in message for fragment in _BUSY_MESSAGES)


def translate_storage_error(exc: BaseException) -> HarnessError:
    """Map a raw storage exception to a canonical :class:`HarnessError`.

    Total for ``sqlite3`` and ``OSError`` inputs: it always returns a
    ``HarnessError`` and never re-raises the raw exception. An input that is
    already a ``HarnessError`` is returned unchanged.
    """
    if isinstance(exc, HarnessError):
        return exc
    if isinstance(exc, sqlite3.OperationalError) and _is_busy(exc):
        return DatabaseBusyError(str(exc))
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return DiskFullError(str(exc))
    if isinstance(exc, (sqlite3.Error, OSError)):
        return StorageError(str(exc))
    return StorageError(f"unexpected storage failure: {exc!r}")


@contextmanager
def translate_storage_errors() -> Iterator[None]:
    """Context manager translating raw storage failures on exit.

    Guarantees no raw ``sqlite3``/``OSError`` escapes the block, so no
    unhandled traceback can reach the TUI.
    """
    try:
        yield
    except HarnessError:
        raise
    except (sqlite3.Error, OSError) as exc:
        raise translate_storage_error(exc) from exc
