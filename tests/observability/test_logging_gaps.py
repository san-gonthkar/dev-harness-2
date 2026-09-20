"""Additional observability coverage: exc_info branch, contains_secret."""

from __future__ import annotations

import io
import json
import logging

import pytest

from dev_harness.observability.logging import JsonFormatter, RedactFilter
from dev_harness.observability.redact import contains_secret

pytestmark = pytest.mark.unit


def test_json_formatter_includes_exception() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(correlation_id="corr-exc"))
    handler.addFilter(RedactFilter())
    logger = logging.getLogger("test.exc")
    logger.handlers = [handler]
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("failed with %s", "sk-ant-api03-ABCDEF1234567890XYZ")
    line = stream.getvalue().strip()
    payload = json.loads(line)
    assert "exc" in payload
    assert "ValueError" in payload["exc"]
    assert "sk-ant-api03-ABCDEF1234567890XYZ" not in payload["exc"]


def test_contains_secret() -> None:
    assert contains_secret("the key is abc", "abc") is True
    assert contains_secret("nothing here", "abc") is False


def test_redact_filter_args_tuple() -> None:
    """RedactFilter redacts string args in the record."""
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="key %s",
        args=("sk-ant-api03-ABCDEF1234567890XYZ",),
        exc_info=None,
    )
    filt = RedactFilter()
    assert filt.filter(record) is True
    # The msg format string has no secret; the arg is redacted.
    assert "sk-ant-api03-ABCDEF1234567890XYZ" not in record.args[0]
    assert "***REDACTED***" in record.args[0]
