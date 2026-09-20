"""Error mapping tests (V11 3.3)."""

from __future__ import annotations

import httpx
import pytest

from dev_harness.contracts.errors import (
    AuthError,
    ContextOverflowError,
    ProviderOverloadedError,
    RateLimitedError,
    TransientError,
)
from dev_harness.providers.errors import fault_table, map_exception, map_status

pytestmark = pytest.mark.negative


def test_429_maps_to_rate_limited() -> None:
    err = map_status(429, retry_after=30)
    assert isinstance(err, RateLimitedError)
    assert err.retryable is True
    assert err.retry_after == 30


def test_529_maps_to_overloaded() -> None:
    err = map_status(529)
    assert isinstance(err, ProviderOverloadedError)
    assert err.retryable is True


def test_401_maps_to_auth() -> None:
    err = map_status(401)
    assert isinstance(err, AuthError)
    assert err.retryable is False


def test_403_maps_to_auth() -> None:
    err = map_status(403)
    assert isinstance(err, AuthError)
    assert err.retryable is False


def test_400_maps_to_context_overflow() -> None:
    err = map_status(400)
    assert isinstance(err, ContextOverflowError)
    assert err.retryable is False


def test_500_maps_to_transient() -> None:
    err = map_status(500)
    assert isinstance(err, TransientError)
    assert err.retryable is True


def test_502_503_504_transient() -> None:
    for code in (502, 503, 504):
        assert isinstance(map_status(code), TransientError)


def test_unknown_status_transient() -> None:
    assert isinstance(map_status(418), TransientError)


def test_map_exception_timeout() -> None:
    exc = httpx.ConnectTimeout("timed out")
    err = map_exception(exc)
    assert isinstance(err, TransientError)
    assert err.retryable is True


def test_map_exception_http_status() -> None:
    request = httpx.Request("POST", "http://fake")
    response = httpx.Response(429, request=request, headers={"Retry-After": "30"})
    exc = httpx.HTTPStatusError("429", request=request, response=response)
    err = map_exception(exc)
    assert isinstance(err, RateLimitedError)
    assert err.retry_after == 30


def test_map_exception_transport() -> None:
    exc = httpx.ConnectError("refused")
    err = map_exception(exc)
    assert isinstance(err, TransientError)


def test_map_exception_unknown() -> None:
    err = map_exception(ValueError("boom"))
    assert isinstance(err, TransientError)


def test_retry_after_invalid_header() -> None:
    request = httpx.Request("POST", "http://fake")
    response = httpx.Response(429, request=request, headers={"Retry-After": "abc"})
    exc = httpx.HTTPStatusError("429", request=request, response=response)
    err = map_exception(exc)
    assert isinstance(err, RateLimitedError)
    assert err.retry_after == 0.0  # invalid header -> default


def test_fault_table_matches_errors() -> None:
    table = fault_table()
    assert len(table) == 9
    by_code = {row["code"]: row for row in table}
    assert by_code[429]["error"] == "RateLimitedError"
    assert by_code[429]["retryable"] is True
    assert by_code[401]["retryable"] is False
    assert by_code[500]["error"] == "TransientError"
