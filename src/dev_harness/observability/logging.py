"""Structured JSON logging with correlation and redaction (V11 0.14)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from dev_harness.observability.redact import redact


class RedactFilter(logging.Filter):
    """Redact secret patterns from every log record's message."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            record.args = tuple(redact(str(a)) if isinstance(a, str) else a for a in record.args)
        return True


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON with correlation id."""

    def __init__(self, correlation_id: str | None = None) -> None:
        super().__init__()
        self.correlation_id = correlation_id or uuid.uuid4().hex[:12]

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": record.created,
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": self.correlation_id,
            "message": redact(record.getMessage()),
        }
        exc = record.exc_info
        if exc:
            payload["exc"] = redact(self.formatException(exc))
        return json.dumps(payload)


def get_logger(
    name: str,
    *,
    correlation_id: str | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Build a structured, redacting logger."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter(correlation_id))
        handler.addFilter(RedactFilter())
        logger.addHandler(handler)
    return logger
