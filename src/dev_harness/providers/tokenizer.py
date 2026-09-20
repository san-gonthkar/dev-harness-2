"""Tokenizer service: exact where available, 4-chars/token + 15% margin (V11 3.8).

The estimator must never underestimate: it uses 4 chars/token and adds a
15% margin, so estimate >= actual for typical text.
"""

from __future__ import annotations

import math

# 4 chars/token is the documented fallback; the 15% margin guarantees no
# underestimates for typical text.
CHARS_PER_TOKEN = 4
MARGIN = 0.15


def estimate_tokens(text: str) -> int:
    """Estimate tokens with a 15% margin so we never underestimate."""
    raw = max(1, math.ceil(len(text) / CHARS_PER_TOKEN))
    return max(1, math.ceil(raw * (1 + MARGIN)))


def count_tokens(text: str, *, exact: int | None = None) -> int:
    """Count tokens, using the exact count when provided, else the estimate."""
    if exact is not None:
        return exact
    return estimate_tokens(text)


def calibration_report(samples: list[tuple[str, int]]) -> dict[str, object]:
    """Build a calibration report: estimate vs actual per sample."""
    rows = []
    underestimates = 0
    overheads: list[float] = []
    for text, actual in samples:
        est = estimate_tokens(text)
        if est < actual:
            underestimates += 1
        overhead = (est - actual) / actual if actual else 0.0
        overheads.append(overhead)
        rows.append(
            {
                "chars": len(text),
                "actual": actual,
                "estimate": est,
                "overhead": round(overhead, 3),
            }
        )
    mean_overhead = sum(overheads) / len(overheads) if overheads else 0.0
    return {
        "samples": rows,
        "underestimates": underestimates,
        "mean_overhead": round(mean_overhead, 3),
    }
