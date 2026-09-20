"""Tokenizer estimator tests (V11 3.8)."""

from __future__ import annotations

import pytest

from dev_harness.providers.tokenizer import (
    calibration_report,
    count_tokens,
    estimate_tokens,
)

pytestmark = pytest.mark.unit

# 20 corpus samples: (text, actual_tokens). Actual is the 4-chars/token count
# WITHOUT margin (the "true" tokenizer count for typical English text).
CORPUS: list[tuple[str, int]] = [
    ("hello world", 3),
    ("The quick brown fox jumps over the lazy dog", 10),
    ("def add(a, b): return a + b", 8),
    ("import os; print(os.getcwd())", 7),
    ("class Foo:\n    def bar(self):\n        return 42", 12),
    ("a" * 100, 25),
    ("b" * 40, 10),
    ("c" * 8, 2),
    ("d" * 4, 1),
    ("e" * 200, 50),
    ("f" * 16, 4),
    ("g" * 12, 3),
    ("h" * 28, 7),
    ("i" * 36, 9),
    ("j" * 44, 11),
    ("k" * 52, 13),
    ("l" * 60, 15),
    ("m" * 68, 17),
    ("n" * 76, 19),
    ("o" * 84, 21),
]


def test_estimate_never_underestimates() -> None:
    """Estimate >= actual for all 20 corpus samples (0 underestimates)."""
    for text, actual in CORPUS:
        est = estimate_tokens(text)
        assert est >= actual, f"underestimate for {text!r}: {est} < {actual}"


def test_mean_overhead_at_most_30_percent() -> None:
    """Mean overhead <= 30%."""
    report = calibration_report(CORPUS)
    assert report["underestimates"] == 0
    assert report["mean_overhead"] <= 0.30


def test_count_tokens_exact() -> None:
    assert count_tokens("hello", exact=7) == 7


def test_count_tokens_estimate() -> None:
    assert count_tokens("hello") == estimate_tokens("hello")


def test_empty_text_min_one() -> None:
    # Empty text: raw = 1, with 15% margin -> 2.
    assert estimate_tokens("") == 2


def test_calibration_report_shape() -> None:
    report = calibration_report([("hello", 3)])
    assert report["samples"][0]["chars"] == 5
    assert report["samples"][0]["actual"] == 3
    assert report["samples"][0]["estimate"] >= 3
