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

stop_driver() {
    if [ -n "$DAEMON_PID" ]; then
        kill "$DAEMON_PID" 2>/dev/null || true
        wait "$DAEMON_PID" 2>/dev/null || true
        DAEMON_PID=""
    fi
    rm -f "$CTL" "$STREAM"
}
trap 'stop_driver' EXIT

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
from dev_harness.core.task_registry import TaskRegistry

THREAD_ID = "t1"
WORKLOAD_SIZE = 5


class Driver:
    """The P6 interrupt engine, driven over a line-JSON control socket."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.gatekeeper = CriticGatekeeper(initial=ExecutionState.READY)
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
                return {
                    "ok": True,
                    "state": self.gatekeeper.state.value,
                    "already": envelope.payload.already,
                    "cancelled": had_tasks,
                    "leftover": len(leftover),
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

# TODO(6.8c): steps 3-6 (hostile interrupt, idempotency, illegal transition, seal)
# TODO(6.8d): steps 7-10 (resume correctness, latency-drill, mutation gate, emit)

echo "P6 acceptance (partial: steps 1-2) OK"
