#!/usr/bin/env bash
# Phase 6 acceptance protocol (V11 6.D) - POSIX/WSL2 twin.
# Requires a POSIX host (WSL2 per plan R2): the driver binds AF_UNIX sockets
# and the hostile-interrupt step (3) escalates with killpg, neither of which
# native Windows Python (<=3.12) can do.
# Deliverable: an interrupt subsystem that reliably kills a hostile process
# tree and seals state, demonstrable against a purpose-built stubborn workload.
# Scope boundary: no pipeline nodes, no UI buttons - commands arrive over IPC.
#
# The driver embedded below composes the real P6 parts (CriticGatekeeper,
# CriticCommandHandler, TaskRegistry, PauseSealer, InterruptMetrics) and serves
# two AF_UNIX sockets: a line-JSON control socket and a streaming socket that
# replays every INTERRUPT_ACK envelope emitted so far, then live ones.
set -euo pipefail
cd "$(dirname "$0")/.."

WS="${TMPDIR:-/tmp}/dev-harness-p6-w1"
DRIVER="${TMPDIR:-/tmp}/dev-harness-p6-driver.py"
LOG="${TMPDIR:-/tmp}/dev-harness-p6-driver.log"
CTL="${TMPDIR:-/tmp}/dev-harness-p6-ctl.sock"
STREAM="${TMPDIR:-/tmp}/dev-harness-p6-stream.sock"
DAEMON_PID=""
RUNNER_PID=""
RUNNER_OUT="${TMPDIR:-/tmp}/dev-harness-p6-runner.out"

stop_runner() {
    if [ -n "$RUNNER_PID" ]; then
        kill -TERM "$RUNNER_PID" 2>/dev/null || true
        kill -KILL "$RUNNER_PID" 2>/dev/null || true
        wait "$RUNNER_PID" 2>/dev/null || true
        RUNNER_PID=""
    fi
}

stop_driver() {
    if [ -n "$DAEMON_PID" ]; then
        kill "$DAEMON_PID" 2>/dev/null || true
        wait "$DAEMON_PID" 2>/dev/null || true
        DAEMON_PID=""
    fi
    rm -f "$CTL" "$STREAM"
}
trap 'stop_runner; stop_driver' EXIT

# --- embedded driver: compose the P6 interrupt engine from its real parts ---
cat >"$DRIVER" <<'PY'
"""P6 acceptance driver: composes the real interrupt-engine parts over AF_UNIX.

Serves a line-JSON control socket (request -> response) and a streaming socket
that, on connect, replays every INTERRUPT_ACK envelope emitted so far and then
streams live ones, one JSON line each.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import threading
import time
from pathlib import Path

from dev_harness.contracts.enums import CriticCommand, ExecutionState
from dev_harness.contracts.errors import IllegalTransitionError
from dev_harness.contracts.events import Envelope
from dev_harness.core.critic import CriticGatekeeper
from dev_harness.core.critic_commands import CriticCommandHandler
from dev_harness.core.metrics import InterruptMetrics
from dev_harness.core.pause_seal import PauseSealer
from dev_harness.core.process_group import ProcessGroupManager
from dev_harness.core.signals import EscalatingInterrupt
from dev_harness.core.task_registry import TaskRegistry

THREAD_ID = "t1"
WORKLOAD_SIZE = 5


class Driver:
    """The P6 interrupt engine, driven over a line-JSON control socket."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        # The protocol drives a live session, so the gatekeeper starts RUNNING
        # (a PAUSE from READY is a no-op per the 0.22 table).
        self.gatekeeper = CriticGatekeeper(initial=ExecutionState.RUNNING)
        self.registry = TaskRegistry()
        self.sealer = PauseSealer(workspace)
        self.metrics = InterruptMetrics()
        self.acks: list[str] = []
        self.stream_clients: list[socket.socket] = []
        self._ack_lock = threading.Lock()
        self._stop = threading.Event()
        self.handler = CriticCommandHandler(
            gatekeeper=self.gatekeeper, emit=self._emit
        )
        self.loop = asyncio.new_event_loop()

    # -- envelope sink -------------------------------------------------------
    def _emit(self, envelope: Envelope) -> None:
        line = envelope.model_dump_json()
        with self._ack_lock:
            self.acks.append(line)
            clients = list(self.stream_clients)
        for conn in clients:
            try:
                conn.sendall((line + "\n").encode("utf-8"))
            except OSError:
                pass

    # -- asyncio workload ----------------------------------------------------
    async def _sleeper(self) -> None:
        while True:
            await asyncio.sleep(0.05)

    def _start_workload(self, count: int) -> int:
        async def make() -> int:
            for _ in range(count):
                task = self.loop.create_task(self._sleeper())
                self.registry.register(THREAD_ID, task)
            return count

        return asyncio.run_coroutine_threadsafe(make(), self.loop).result(5.0)

    def _cancel_all(self) -> list[asyncio.Task[object]]:
        return asyncio.run_coroutine_threadsafe(
            self.registry.cancel_all(THREAD_ID), self.loop
        ).result(5.0)

    # -- control socket (line JSON) -----------------------------------------
    def serve_control(self, ctl_path: str) -> None:
        ctl = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if Path(ctl_path).exists():
            Path(ctl_path).unlink()
        ctl.bind(ctl_path)
        ctl.listen(4)
        self.ctl = ctl
        threading.Thread(target=self._ctl_loop, daemon=True).start()

    def _ctl_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.ctl.accept()
            except OSError:
                return
            threading.Thread(target=self._ctl_conn, args=(conn,), daemon=True).start()

    def _ctl_conn(self, conn: socket.socket) -> None:
        with conn:
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            req = json.loads(data.split(b"\n", 1)[0])
            conn.sendall((json.dumps(self.dispatch(req)) + "\n").encode("utf-8"))

    # -- streaming socket (replay + live) -----------------------------------
    def serve_stream(self, stream_path: str) -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if Path(stream_path).exists():
            Path(stream_path).unlink()
        srv.bind(stream_path)
        srv.listen(8)
        self.stream_srv = srv
        threading.Thread(target=self._stream_loop, daemon=True).start()

    def _stream_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.stream_srv.accept()
            except OSError:
                return
            threading.Thread(target=self._stream_conn, args=(conn,), daemon=True).start()

    def _stream_conn(self, conn: socket.socket) -> None:
        # Snapshot + register under one lock so no ACK is lost or duplicated
        # between the replay and the live stream.
        with self._ack_lock:
            replay = list(self.acks)
            self.stream_clients.append(conn)
        try:
            for line in replay:
                conn.sendall((line + "\n").encode("utf-8"))
            while not self._stop.is_set():
                time.sleep(0.05)
        except OSError:
            pass
        finally:
            with self._ack_lock:
                if conn in self.stream_clients:
                    self.stream_clients.remove(conn)
            try:
                conn.close()
            except OSError:
                pass

    # -- dispatch ------------------------------------------------------------
    def dispatch(self, req: dict) -> dict:
        cmd = req.get("cmd")
        try:
            if cmd == "status":
                return {
                    "ok": True,
                    "state": self.gatekeeper.state.value,
                    "tasks": self.registry.count(THREAD_ID),
                }
            if cmd == "start-workload":
                count = self._start_workload(int(req.get("count", WORKLOAD_SIZE)))
                return {"ok": True, "tasks": count}
            if cmd == "pause":
                had_tasks = self.registry.count(THREAD_ID) > 0
                start = time.monotonic()
                envelope = self.handler.handle(CriticCommand.PAUSE)
                leftover = self._cancel_all()
                elapsed_ms = (time.monotonic() - start) * 1000.0
                self.metrics.record_interrupt(elapsed_ms)
                seal = self.sealer.seal()
                return {
                    "ok": True,
                    "state": self.gatekeeper.state.value,
                    "already": envelope.payload.already,
                    "cancelled": had_tasks,
                    "leftover": len(leftover),
                    "seal": {
                        "is_paused": seal.is_paused,
                        "timestamp": seal.timestamp,
                        "checkpoint_hash": seal.checkpoint_hash,
                    },
                }
            if cmd == "resume":
                envelope = self.handler.handle(CriticCommand.RESUME)
                return {
                    "ok": True,
                    "state": self.gatekeeper.state.value,
                    "already": envelope.payload.already,
                }
            if cmd == "stop":
                envelope = self.handler.handle(CriticCommand.STOP)
                return {
                    "ok": True,
                    "state": self.gatekeeper.state.value,
                    "already": envelope.payload.already,
                }
            if cmd == "interrupt-hostile":
                # Escalate SIGINT -> grace 3.0s -> SIGKILL over the caller's
                # process group, then reap. The manager is only used to forget
                # the PGID; the group itself is signalled directly.
                pgid = int(req["pgid"])
                result = EscalatingInterrupt(
                    ProcessGroupManager(), grace=3.0
                ).interrupt(pgid)
                return {
                    "ok": True,
                    "sigint_sent": result.sigint_sent,
                    "sigkill_sent": result.sigkill_sent,
                    "reaped": result.reaped,
                }
            if cmd == "shutdown":
                self._stop.set()
                self.loop.call_soon_threadsafe(self.loop.stop)
                return {"ok": True}
            return {"ok": False, "error": f"unknown cmd {cmd}"}
        except IllegalTransitionError:
            # An illegal pair must not crash the driver; report it and leave
            # the gatekeeper state unchanged.
            return {
                "ok": False,
                "error": "IllegalTransitionError",
                "state": self.gatekeeper.state.value,
            }


def main() -> int:
    parser = argparse.ArgumentParser(prog="p6-driver")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--ctl", required=True)
    parser.add_argument("--stream", required=True)
    args = parser.parse_args()

    driver = Driver(args.workspace)
    driver.serve_control(args.ctl)
    driver.serve_stream(args.stream)
    try:
        driver.loop.run_forever()
    finally:
        driver.loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY

run_driver() {
    python "$DRIVER" --workspace "$WS" --ctl "$CTL" --stream "$STREAM" \
        >"$LOG" 2>&1 &
    DAEMON_PID=$!
    for _ in $(seq 1 100); do
        [ -S "$CTL" ] && [ -S "$STREAM" ] && break
        sleep 0.1
    done
    if [ ! -S "$CTL" ] || [ ! -S "$STREAM" ]; then
        echo "driver did not bind sockets within 10s; log:" >&2
        cat "$LOG" >&2
        exit 1
    fi
}

ctl() { python -c "
import json, socket, sys
c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
c.connect('$CTL')
c.sendall((json.dumps(json.loads('$1')) + '\n').encode())
d = b''
while b'\n' not in d:
    chunk = c.recv(4096)
    if not chunk:
        break
    d += chunk
sys.stdout.write(d.decode())
"; }

# --- ACK recorder: reads `count` INTERRUPT_ACK lines from the stream socket --
ACK_RECORDER="${TMPDIR:-/tmp}/dev-harness-p6-ackrec.py"
cat >"$ACK_RECORDER" <<'PY'
"""Reads `count` INTERRUPT_ACK lines from the streaming socket."""
from __future__ import annotations

import argparse
import socket
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(prog="p6-ackrec")
    parser.add_argument("--stream", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()

    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(args.stream)
    lines: list[str] = []
    buf = b""
    try:
        while len(lines) < args.count:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf and len(lines) < args.count:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    lines.append(line.decode("utf-8"))
    except OSError:
        pass
    finally:
        conn.close()
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY

rm -rf "$WS"
mkdir -p "$WS"
( cd "$WS" && git init -q -b main && git commit -q --allow-empty -m "p6 init" )

echo "[P6] step 1: transition table print (16 cells, no UNDEFINED)"
table="$(python -m dev_harness.core.cli transitions --table)"
echo "$table"
for state in READY RUNNING PAUSED STOPPED; do
    grep -q "$state" <<<"$table" || { echo "table missing state $state" >&2; exit 1; }
done
for command in START PAUSE RESUME STOP; do
    grep -q "$command" <<<"$table" || { echo "table missing command $command" >&2; exit 1; }
done
if grep -q "UNDEFINED" <<<"$table"; then
    echo "table contains an UNDEFINED cell" >&2
    exit 1
fi

echo "[P6] step 2: cooperative interrupt (PAUSE a sleeping asyncio workload)"
run_driver
workload="$(ctl '{"cmd":"start-workload","kind":"cooperative"}')"
echo "  start-workload -> $workload"
grep -q '"ok": true' <<<"$workload" || { echo "start-workload failed: $workload" >&2; exit 1; }
t0="$(date +%s%N)"
pause="$(ctl '{"cmd":"pause"}')"
t1="$(date +%s%N)"
elapsed_ms=$(( (t1 - t0) / 1000000 ))
echo "  pause -> $pause (${elapsed_ms}ms)"
grep -q '"state": "PAUSED"' <<<"$pause" || { echo "pause did not reach PAUSED: $pause" >&2; exit 1; }
grep -q '"cancelled": true' <<<"$pause" || { echo "pause did not cancel tasks: $pause" >&2; exit 1; }
grep -q '"leftover": 0' <<<"$pause" || { echo "pause left tasks running: $pause" >&2; exit 1; }
if [ "$elapsed_ms" -ge 500 ]; then
    echo "pause latency ${elapsed_ms}ms exceeded the 500ms SLO" >&2
    exit 1
fi

echo "[P6] step 3: hostile interrupt (SIGINT ignored -> SIGKILL over the group)"
rm -f "$RUNNER_OUT" "$RUNNER_OUT".child* "$RUNNER_OUT".stop
python tests/support/stubborn_runner.py --output "$RUNNER_OUT" --children 3 --new-session &
RUNNER_PID=$!
for _ in $(seq 1 100); do
    [ -s "$RUNNER_OUT" ] && break
    sleep 0.1
done
if [ ! -s "$RUNNER_OUT" ]; then
    echo "stubborn runner produced no output within 10s" >&2
    exit 1
fi
PGID="$(ps -o pgid= -p "$RUNNER_PID" | tr -d ' ')"
echo "  runner pid=$RUNNER_PID pgid=$PGID"
size_before="$(wc -c <"$RUNNER_OUT")"
hostile="$(ctl "{\"cmd\":\"interrupt-hostile\",\"pgid\":$PGID}")"
echo "  interrupt-hostile -> $hostile"
grep -q '"ok": true' <<<"$hostile" || { echo "interrupt-hostile failed: $hostile" >&2; exit 1; }
grep -q '"sigint_sent": true' <<<"$hostile" || { echo "SIGINT was not sent: $hostile" >&2; exit 1; }
grep -q '"sigkill_sent": true' <<<"$hostile" || { echo "SIGKILL was not sent (group died on SIGINT?): $hostile" >&2; exit 1; }
# The runner is our child: reap it so it is not left a zombie, then confirm the
# whole group has drained (bounded poll).
wait "$RUNNER_PID" 2>/dev/null || true
stat="$(ps -o stat= -p "$RUNNER_PID" 2>/dev/null | tr -d ' ' || true)"
if [ -n "$stat" ] && [[ "$stat" == *Z* ]]; then
    echo "runner $RUNNER_PID left a zombie (stat=$stat)" >&2
    exit 1
fi
RUNNER_PID=""
gone=0
for _ in $(seq 1 40); do
    if ! pgrep -g "$PGID" >/dev/null 2>&1; then gone=1; break; fi
    sleep 0.1
done
[ "$gone" -eq 1 ] || { echo "process group $PGID still alive after 4s" >&2; exit 1; }
# The output file must have stopped growing.
sleep 0.5
size_after="$(wc -c <"$RUNNER_OUT")"
if [ "$size_after" -ne "$size_before" ]; then
    echo "runner output still growing ($size_before -> $size_after)" >&2
    exit 1
fi

echo "[P6] step 4: idempotency (3x PAUSE -> one transition, 3 ACKs, 2 already)"
# Restart the driver so the streaming socket replays no stale ACKs from step 2
# and the gatekeeper starts RUNNING (so the first PAUSE is a real transition).
stop_driver
run_driver
ACK_OUT="${TMPDIR:-/tmp}/dev-harness-p6-acks.txt"
rm -f "$ACK_OUT"
python "$ACK_RECORDER" --stream "$STREAM" --out "$ACK_OUT" --count 3 &
ACK_PID=$!
p1="$(ctl '{"cmd":"pause"}')"
p2="$(ctl '{"cmd":"pause"}')"
p3="$(ctl '{"cmd":"pause"}')"
wait "$ACK_PID" 2>/dev/null || true
echo "  pause#1 -> $p1"
echo "  pause#2 -> $p2"
echo "  pause#3 -> $p3"
grep -q '"state": "PAUSED"' <<<"$p1" || { echo "pause#1 did not reach PAUSED: $p1" >&2; exit 1; }
grep -Eq '"already": ?false' <<<"$p1" || { echo "pause#1 was not a real transition: $p1" >&2; exit 1; }
grep -Eq '"already": ?true' <<<"$p2" || { echo "pause#2 was not idempotent: $p2" >&2; exit 1; }
grep -Eq '"already": ?true' <<<"$p3" || { echo "pause#3 was not idempotent: $p3" >&2; exit 1; }
ack_lines="$(wc -l <"$ACK_OUT")"
[ "$ack_lines" -eq 3 ] || { echo "expected 3 INTERRUPT_ACK envelopes, got $ack_lines" >&2; cat "$ACK_OUT" >&2; exit 1; }
sed -n '1p' "$ACK_OUT" | grep -Eq '"already": ?false' || { echo "ACK#1 was not a real transition" >&2; exit 1; }
sed -n '2p' "$ACK_OUT" | grep -Eq '"already": ?true' || { echo "ACK#2 missing already:true" >&2; exit 1; }
sed -n '3p' "$ACK_OUT" | grep -Eq '"already": ?true' || { echo "ACK#3 missing already:true" >&2; exit 1; }

echo "[P6] step 5: illegal transition (STOP then RESUME -> IllegalTransitionError)"
stop="$(ctl '{"cmd":"stop"}')"
echo "  stop -> $stop"
grep -q '"state": "STOPPED"' <<<"$stop" || { echo "stop did not reach STOPPED: $stop" >&2; exit 1; }
resume="$(ctl '{"cmd":"resume"}')"
echo "  resume -> $resume"
grep -q '"ok": false' <<<"$resume" || { echo "resume from STOPPED did not fail: $resume" >&2; exit 1; }
grep -q '"error": "IllegalTransitionError"' <<<"$resume" || { echo "resume error was not IllegalTransitionError: $resume" >&2; exit 1; }
status="$(ctl '{"cmd":"status"}')"
echo "  status -> $status"
grep -q '"state": "STOPPED"' <<<"$status" || { echo "state changed after illegal resume: $status" >&2; exit 1; }

echo "[P6] step 6: pause seal (is_paused, HEAD hash, timestamp)"
# Step 5 left the gatekeeper STOPPED (PAUSE is illegal there), so restart the
# driver for a fresh RUNNING -> PAUSED cycle and observe the seal.
stop_driver
run_driver
head_sha="$(git -C "$WS" rev-parse HEAD)"
t_before="$(date +%s)"
seal_pause="$(ctl '{"cmd":"pause"}')"
t_after="$(date +%s)"
echo "  pause -> $seal_pause"
grep -q '"is_paused": true' <<<"$seal_pause" || { echo "seal is_paused not true: $seal_pause" >&2; exit 1; }
grep -q "\"checkpoint_hash\": \"$head_sha\"" <<<"$seal_pause" || { echo "seal hash != HEAD ($head_sha): $seal_pause" >&2; exit 1; }
ts="$(python -c "import json,sys; print(json.loads(sys.argv[1])['seal']['timestamp'])" "$seal_pause")"
python -c "
import sys
ts = float(sys.argv[1]); before = int(sys.argv[2]); after = int(sys.argv[3])
if not (before - 1 <= ts <= after + 1):
    raise SystemExit(f'seal timestamp {ts} outside [{before - 1}, {after + 1}]')
" "$ts" "$t_before" "$t_after" || { echo "seal timestamp out of range: $ts" >&2; exit 1; }

# TODO(6.8d): steps 7-10 (resume correctness, latency-drill, mutation gate, emit)

echo "P6 acceptance (partial: steps 1-6) OK"
