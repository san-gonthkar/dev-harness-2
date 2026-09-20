"""Event-producer ownership check (V11 0.20 / 2.5).

Asserts 9/9 EventType members have at least one producer task and one consumer
task. A member with no producer fails the build.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Ownership matrix (V11 2.5): EventType -> (producers, consumers, first phase).
OWNERSHIP: dict[str, tuple[list[str], list[str], str]] = {
    "FILE_CHANGE": (["5.11"], ["7.2"], "P5"),
    "GIT_STATUS_UPDATE": (["5.11"], ["7.2"], "P5"),
    "AGENT_TOKEN_STREAM": (["3.9"], ["7.3"], "P3"),
    "TEST_PROGRESS": (["8.11"], ["7.3"], "P8"),
    "MODEL_CONFIG_CHANGE": (["3.7", "5.5"], ["7.5"], "P3"),
    "INTERRUPT_REQUEST": (["7.6", "4.7"], ["6.2"], "P4"),
    "INTERRUPT_ACK": (["6.2"], ["7.6"], "P6"),
    "METRICS_UPDATE": (["4.10"], ["7.5"], "P4"),
    "SNAPSHOT": (["5.8"], ["7.7"], "P5"),
}


def check() -> list[str]:
    """Return event types missing a producer or consumer."""
    gaps: list[str] = []
    from dev_harness.contracts.enums import EventType

    members = {e.value for e in EventType}
    for member in members:
        if member not in OWNERSHIP:
            gaps.append(f"{member}: not in ownership matrix")
            continue
        producers, consumers, _ = OWNERSHIP[member]
        if not producers:
            gaps.append(f"{member}: no producer task")
        if not consumers:
            gaps.append(f"{member}: no consumer task")
    # Also flag matrix entries with no enum member (stale).
    for member in OWNERSHIP:
        if member not in members:
            gaps.append(f"{member}: stale matrix entry (not an EventType)")
    return gaps


def main() -> int:
    gaps = check()
    if gaps:
        for g in gaps:
            print(f"event ownership FAIL: {g}", file=sys.stderr)
        return 1
    print("event ownership OK: 9/9 EventType members have producer + consumer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
