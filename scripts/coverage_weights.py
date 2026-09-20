"""Coverage weight derivation (V11 0.21 / 2.3).

Pins the published per-package weights and recomputes the overall gate.
Fails on drift from the published 89/83.
"""

from __future__ import annotations

import sys

# (package, weight, line_target, branch_target) - V11 2.3 ledger.
WEIGHTS: list[tuple[str, float, float, float]] = [
    ("contracts", 4.0, 100.0, 95.0),
    ("storage", 12.0, 95.0, 90.0),
    ("vcs", 7.0, 95.0, 90.0),
    ("broker", 10.0, 80.0, 80.0),
    ("core", 10.0, 95.0, 90.0),
    ("ipc", 7.0, 92.0, 85.0),
    ("providers", 12.0, 90.0, 85.0),
    ("engine", 22.0, 88.0, 80.0),
    ("recovery", 4.0, 90.0, 85.0),
    ("observability", 5.0, 90.0, 80.0),
    ("tui", 7.0, 75.0, 65.0),
]

PUBLISHED_LINE = 89.0
PUBLISHED_BRANCH = 83.0


def derive_gate() -> tuple[float, float]:
    """Weighted overall line/branch gate, rounded down to the whole point."""
    total_weight = sum(w for _, w, _, _ in WEIGHTS)
    line = sum(w * lt for _, w, lt, _ in WEIGHTS) / total_weight
    branch = sum(w * bt for _, w, _, bt in WEIGHTS) / total_weight
    return int(line), int(branch)


def main() -> int:
    line, branch = derive_gate()
    if (line, branch) != (PUBLISHED_LINE, PUBLISHED_BRANCH):
        print(
            f"coverage weight drift: derived {line}/{branch}, published {PUBLISHED_LINE}/{PUBLISHED_BRANCH}",
            file=sys.stderr,
        )
        return 1
    print(f"coverage weights OK: overall gate {line}% line / {branch}% branch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
