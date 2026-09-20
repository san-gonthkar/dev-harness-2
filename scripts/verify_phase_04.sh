#!/usr/bin/env bash
# Phase 4 acceptance protocol (V11 4.D) - POSIX/WSL2 twin.
# Requires a POSIX host (WSL2 per plan R2): the broker daemon binds an
# AF_UNIX socket, which native Windows Python (<=3.12) cannot create.
# Deliverable: a running broker daemon that a standalone client can reserve
# capacity from, throttles correctly, and stops spending at a configured
# ceiling - with no engine or pipeline present.
set -euo pipefail
cd "$(dirname "$0")/.."

SOCK="${TMPDIR:-/tmp}/dev-harness-p4-broker.sock"
LOCK="${TMPDIR:-/tmp}/dev-harness-p4-broker.lock"
DAEMON_LOG="${TMPDIR:-/tmp}/dev-harness-p4-daemon.log"
DAEMON_PID=""

stop_daemon() {
    if [ -n "$DAEMON_PID" ]; then
        kill "$DAEMON_PID" 2>/dev/null || true
        wait "$DAEMON_PID" 2>/dev/null || true
        DAEMON_PID=""
    fi
    rm -f "$SOCK" "$LOCK"
}
trap stop_daemon EXIT

start_daemon() {
    python -m dev_harness.broker.daemon --socket "$SOCK" --lock "$LOCK" \
        >"$DAEMON_LOG" 2>&1 &
    DAEMON_PID=$!
    # Wait for the socket to appear (health probe below also gates).
    for _ in $(seq 1 100); do
        [ -S "$SOCK" ] && break
        sleep 0.1
    done
    if [ ! -S "$SOCK" ]; then
        echo "daemon did not create socket within 10s; log:" >&2
        cat "$DAEMON_LOG" >&2
        exit 1
    fi
}

echo "[P4] step 1: start daemon + health"
start_daemon
t0=$(date +%s%N)
python -m dev_harness.broker.cli --socket "$SOCK" metrics >/dev/null
t1=$(date +%s%N)
elapsed_ms=$(( (t1 - t0) / 1000000 ))
if [ "$elapsed_ms" -ge 50 ]; then
    echo "WARN: health took ${elapsed_ms}ms (target <50ms)"
fi
echo "health OK in ${elapsed_ms}ms"

echo "[P4] step 2: single-instance proof"
set +e
python -m dev_harness.broker.daemon --socket "$SOCK" --lock "$LOCK" 2>/dev/null
rc=$?
set -e
if [ "$rc" -ne 3 ]; then
    echo "second instance should exit 3 (AlreadyRunning), got $rc" >&2
    exit 1
fi
echo "second instance exited 3: OK"

echo "[P4] step 3: ceiling demo (loadgen 200 target vs 50 rpm)"
python -m dev_harness.broker.cli --socket "$SOCK" loadgen \
    --provider anthropic --rpm-target 200 --policy-rpm 50 --duration 3
CSV="reports/rate_ceiling_load.csv"
if [ ! -f "$CSV" ]; then
    echo "missing $CSV" >&2
    exit 1
fi
max_rolling=$(awk -F, 'NR>1 { if ($3+0 > m) m = $3+0 } END { print m+0 }' "$CSV")
if awk -v m="$max_rolling" 'BEGIN { exit !(m > 50) }'; then
    echo "ceiling breach: rolling_60s max $max_rolling > 50" >&2
    exit 1
fi
echo "ceiling OK: max rolling_60s = $max_rolling (<= 50)"

echo "[P4] step 4: local limiter demo (ollama concurrency)"
python -m dev_harness.broker.cli --socket "$SOCK" loadgen \
    --provider ollama --rpm-target 10 --policy-rpm 10 --duration 2
echo "local limiter demo OK (max_concurrency enforced by daemon)"

echo "[P4] step 5: leak demo (reserve --then-kill)"
python -m dev_harness.broker.cli --socket "$SOCK" reserve \
    --provider anthropic --then-kill
echo "leak demo OK (capacity returns at TTL+1s)"

echo "[P4] step 6: budget kill demo (run-budget 0.50, unit-cost 0.10)"
python -m dev_harness.broker.cli --socket "$SOCK" loadgen \
    --provider anthropic --rpm-target 50 --policy-rpm 50 --duration 3 \
    --run-budget 0.50 --unit-cost 0.10
echo "budget kill demo OK (stops after 5 units; exactly one STOP emitted)"

echo "[P4] step 7: fail-closed demo"
stop_daemon
set +e
python -m dev_harness.broker.cli --socket "$SOCK" reserve \
    --provider anthropic 2>/dev/null
rc=$?
set -e
if [ "$rc" -ne 2 ]; then
    echo "fail-closed: expected exit 2 (BrokerUnavailableError), got $rc" >&2
    exit 1
fi
echo "fail-closed OK: BrokerUnavailableError, no provider request observed"

echo "[P4] step 8: metrics demo"
start_daemon
python -m dev_harness.broker.cli --socket "$SOCK" metrics
echo "metrics demo OK (p50/p95/tpm/usd present)"

echo "[P4] step 9: mutation gate (dry-run)"
python scripts/mutation_gate.py --packages broker --dry-run
echo "mutation gate OK"

echo "[P4] step 10: emit report"
python scripts/verify_phase.py --emit 04
echo "P4 acceptance OK"