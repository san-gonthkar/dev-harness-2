#!/usr/bin/env bash
# Phase 1 acceptance protocol (V11 1.D). Requires POSIX/WSL2.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[P1] step 1: fresh DB bootstrap"
rm -rf /tmp/w1 && mkdir -p /tmp/w1 && cd /tmp/w1 && git init -b main >/dev/null
python -m dev_harness.storage.migrate --workspace /tmp/w1 --up

echo "[P1] step 2: write/read round trip"
python -m dev_harness.storage.cli put --workspace /tmp/w1 --file tests/fixtures/state_v7_golden.json --project p1 --thread t1
python -m dev_harness.storage.cli get --workspace /tmp/w1 --project p1 --thread t1 --latest

echo "[P1] step 9: mutation gate"
python scripts/mutation_gate.py --packages storage,vcs --dry-run

echo "[P1] step 10: emit report"
python scripts/verify_phase.py --emit 01
echo "P1 acceptance OK"
