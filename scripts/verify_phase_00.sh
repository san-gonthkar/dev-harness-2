#!/usr/bin/env bash
# Phase 0 acceptance protocol (V11 0.D). Requires POSIX/WSL2.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[P0] step 2: full gate"
python -m ruff check src tests scripts
python -m mypy src
python -m pytest tests -q

echo "[P0] step 3: contract freeze"
python -m dev_harness.contracts.schema --emit
diff -q schemas/harness_state.v7.json <(python -m dev_harness.contracts.schema --emit >/dev/null; cat schemas/harness_state.v7.json)

echo "[P0] step 8: event ownership"
python scripts/check_event_ownership.py

echo "[P0] step 9: transition table"
python -m dev_harness.contracts.transitions --table

echo "[P0] step 10: emit report"
python scripts/verify_phase.py --phase 00
echo "P0 acceptance OK"
