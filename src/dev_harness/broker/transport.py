"""Broker transport — re-exports the shared endpoint abstraction.

The implementation lives in :mod:`dev_harness.ipc.transport` so the broker and
the engine share one transport. This module keeps the historical import path
(``dev_harness.broker.transport``) working.
"""

from __future__ import annotations

from dev_harness.ipc.transport import (
    DEFAULT_TCP_PORT,
    EPHEMERAL_PORT,
    LOOPBACK,
    Endpoint,
    TransportError,
    af_unix_available,
    bind_server,
    bound_endpoint,
    cleanup,
    connect,
    default_endpoint,
    parse_endpoint,
)

__all__ = [
    "DEFAULT_TCP_PORT",
    "EPHEMERAL_PORT",
    "LOOPBACK",
    "Endpoint",
    "TransportError",
    "af_unix_available",
    "bind_server",
    "bound_endpoint",
    "cleanup",
    "connect",
    "default_endpoint",
    "parse_endpoint",
]
