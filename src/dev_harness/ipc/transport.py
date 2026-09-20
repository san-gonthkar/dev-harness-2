"""POSIX gate for AF_UNIX transport (V11 2.7).

AF_UNIX sockets are POSIX-only. On Windows the transport refuses to start
with UnsupportedPlatformError naming WSL2 as the supported path.
"""

from __future__ import annotations

import os
import sys

from dev_harness.contracts.errors import HarnessError


class UnsupportedPlatformError(HarnessError):
    """The transport is not supported on this platform."""


def require_posix() -> None:
    """Raise UnsupportedPlatformError on non-POSIX platforms.

    The error message must contain "WSL2" so users know the supported path.
    """
    if os.name != "posix" or sys.platform == "win32":
        raise UnsupportedPlatformError(
            "AF_UNIX IPC requires a POSIX platform; on Windows run inside WSL2",
            remediation="Run the harness inside WSL2 or on a POSIX host.",
        )


def is_posix() -> bool:
    """True when the current platform supports AF_UNIX sockets."""
    return os.name == "posix" and sys.platform != "win32"
