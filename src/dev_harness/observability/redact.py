"""Redaction of secrets from log/artifact text (V11 0.14)."""

from __future__ import annotations

import re

# API key patterns: Anthropic, OpenAI-style, generic long tokens.
_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-ant-api03-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE),
]

REDACTED = "***REDACTED***"


def redact(text: str) -> str:
    """Replace secrets in ``text`` with a redaction marker."""
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def contains_secret(text: str, key: str) -> bool:
    """True if the raw key substring appears in ``text``."""
    return key in text
