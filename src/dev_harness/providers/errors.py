"""HTTP status -> typed provider error mapping (V11 3.3).

This is the file downstream retry logic trusts: every mapped status code
must have a negative test (3.C: providers/errors.py 100% line / 95% branch).
"""

from __future__ import annotations

import httpx

from dev_harness.contracts.errors import (
    AuthError,
    ContextOverflowError,
    ProviderOverloadedError,
    RateLimitedError,
    TransientError,
)

# Status codes that map to a specific typed error.
_STATUS_MAP: dict[
    int,
    type[RateLimitedError | ProviderOverloadedError | AuthError | ContextOverflowError],
] = {
    429: RateLimitedError,
    529: ProviderOverloadedError,
    401: AuthError,
    403: AuthError,
    400: ContextOverflowError,
}


def map_status(
    status_code: int, *, retry_after: float | None = None
) -> (
    RateLimitedError
    | ProviderOverloadedError
    | AuthError
    | ContextOverflowError
    | TransientError
):
    """Map an HTTP status code to a typed provider error.

    429 -> RateLimitedError(retryable=True, retry_after=...)
    529 -> ProviderOverloadedError
    401/403 -> AuthError(retryable=False)
    400 -> ContextOverflowError
    5xx (500, 502, 503, 504) -> TransientError
    anything else -> TransientError
    """
    if status_code in _STATUS_MAP:
        cls = _STATUS_MAP[status_code]
        if cls is RateLimitedError:
            err = RateLimitedError(
                f"rate limited (429), retry after {retry_after or 0}s"
            )
            err.retry_after = retry_after or 0.0
            return err
        return cls(f"provider error {status_code}")
    return TransientError(f"transient provider error {status_code}")


def map_exception(
    exc: Exception,
) -> (
    RateLimitedError
    | ProviderOverloadedError
    | AuthError
    | ContextOverflowError
    | TransientError
):
    """Map an httpx exception to a typed provider error."""
    if isinstance(exc, httpx.TimeoutException):
        return TransientError(f"request timed out: {exc}")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        retry_after = _retry_after(exc.response)
        return map_status(status, retry_after=retry_after)
    if isinstance(exc, httpx.TransportError):
        return TransientError(f"transport error: {exc}")
    return TransientError(f"unexpected error: {exc}")


def _retry_after(response: httpx.Response) -> float | None:
    """Extract the Retry-After header as seconds."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def fault_table() -> list[dict[str, object]]:
    """The documented fault catalogue (used by the CLI fault-drill)."""
    return [
        {"code": 429, "error": "RateLimitedError", "retryable": True},
        {"code": 529, "error": "ProviderOverloadedError", "retryable": True},
        {"code": 401, "error": "AuthError", "retryable": False},
        {"code": 403, "error": "AuthError", "retryable": False},
        {"code": 400, "error": "ContextOverflowError", "retryable": False},
        {"code": 500, "error": "TransientError", "retryable": True},
        {"code": 502, "error": "TransientError", "retryable": True},
        {"code": 503, "error": "TransientError", "retryable": True},
        {"code": 504, "error": "TransientError", "retryable": True},
    ]
