"""Redaction + structured logging tests (V11 0.14)."""

from __future__ import annotations

import io
import json
import logging

import pytest

from dev_harness.observability.logging import JsonFormatter, RedactFilter
from dev_harness.observability.redact import redact

pytestmark = pytest.mark.unit

API_KEY = "sk-ant-api03-ABCDEF1234567890XYZ"


def test_redact_api_key() -> None:
    assert API_KEY not in redact(f"key={API_KEY}")
    assert "***REDACTED***" in redact(f"key={API_KEY}")


def test_redact_generic_sk() -> None:
    key = "sk-" + "a" * 25
    assert "***REDACTED***" in redact(key)
    assert key not in redact(key)


def test_redact_bearer() -> None:
    token = "abc123" * 4
    out = redact(f"Authorization: Bearer {token}")
    assert token not in out
    assert "***REDACTED***" in out


def test_logger_redacts_message() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(correlation_id="corr1"))
    handler.addFilter(RedactFilter())
    logger = logging.getLogger("test.redact2")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("using key %s", API_KEY)
    line = stream.getvalue().strip()
    payload = json.loads(line)
    assert API_KEY not in payload["message"]
    assert "***REDACTED***" in payload["message"]


def test_json_formatter_output() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)

    handler.setFormatter(JsonFormatter(correlation_id="corr123"))
    handler.addFilter(RedactFilter())
    logger = logging.getLogger("test.json")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("hello %s", API_KEY)
    line = stream.getvalue().strip()
    payload = json.loads(line)
    assert payload["correlation_id"] == "corr123"
    assert payload["message"] == "hello ***REDACTED***"
