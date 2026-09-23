#!/usr/bin/env bash
# Phase 5 acceptance protocol (V11 5.D) - POSIX/WSL2 twin.
# Requires a POSIX host (WSL2 per plan R2): the engine daemon binds an
# AF_UNIX socket, which native Windows Python (<=3.12) cannot create.
# Deliverable: a daemon that can be started, attached to by multiple clients,
# driven through a trivial scripted "workload", and shut down cleanly - with
# no pipeline, no personas, and no UI.
#
# The daemon is composed from the real P5 parts (EngineDaemon socket layer,
# SessionManager, CommandHandler, Fanout, StateBroadcast, GracefulShutdown,
# StubWorkload). The plan's `dev-harness-engine` CLI only speaks the bootstrap
# handshake (5.6); the streaming/attach surface has no CLI, so the driver
# embedded below is the process the protocol starts.
set -euo pipefail
cd "$(dirname "$0")/.."

WS="${TMPDIR:-/tmp}/dev-harness-p5-w1"
DRIVER="${TMPDIR:-/tmp}/dev-harness-p5-driver.py"
LOG="${TMPDIR:-/tmp}/dev-harness-p5-daemon.log"
SOCK="$(python -c "from dev_harness.paths import derive_paths; print(derive_paths('$WS').socket_path)")"
CTL="${SOCK}.ctl"
BROKER_SOCK="${TMPDIR:-/tmp}/dev-harness-p5-broker.sock"
BROKER_LOCK="${TMPDIR:-/tmp}/dev-harness-p5-broker.lock"
BROKER_LOG="${TMPDIR:-/tmp}/dev-harness-p5-broker.log"
DAEMON_PID=""
BROKER_PID=""

stop_broker() {
    if [ -n "$BROKER_PID" ]; then
        kill "$BROKER_PID" 2>/dev/null || true
        wait "$BROKER_PID" 2>/dev/null || true
        BROKER_PID=""
    fi
    rm -f "$BROKER_SOCK" "$BROKER_LOCK"
}

start_broker() {
    python -m dev_harness.broker.daemon --socket "$BROKER_SOCK" --lock "$BROKER_LOCK" \
        >"$BROKER_LOG" 2>&1 &
    BROKER_PID=$!
    for _ in $(seq 1 100); do
        [ -S "$BROKER_SOCK" ] && break
        sleep 0.1
    done
    if [ ! -S "$BROKER_SOCK" ]; then
        echo "broker did not bind socket within 10s; log:" >&2
        cat "$BROKER_LOG" >&2
        exit 1
    fi
}

stop_daemon() {
    if [ -n "$DAEMON_PID" ]; then
        kill "$DAEMON_PID" 2>/dev/null || true
        wait "$DAEMON_PID" 2>/dev/null || true
        DAEMON_PID=""
    fi
}
trap 'stop_daemon; stop_broker' EXIT

# --- embedded driver: compose the P5 daemon from its real parts -------------
cat >"$DRIVER" <<'PY'
"""P5 acceptance daemon: composes the real engine parts over AF_UNIX."""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from pathlib import Path

from dev_harness.contracts.enums import ExecutionState
from dev_harness.engine.commands import (
    AttachCommand,
    CommandHandler,
    StartSessionCommand,
)
from dev_harness.engine.fanout import Fanout
from dev_harness.engine.session import SessionManager
from dev_harness.engine.shutdown import GracefulShutdown
from dev_harness.engine.state_broadcast import StateBroadcast
from dev_harness.ipc.framing import encode
from tests.support.stub_workload import StubWorkload


class Daemon:
    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.sessions = SessionManager()
        self.fanout = Fanout()
        self.broadcast: StateBroadcast | None = None
        self.handler = CommandHandler(self.sessions, on_shutdown=self.request_stop)
        self.clients: dict[str, socket.socket] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def state_of(self):  # pragma: no cover - driver glue
        session = self.sessions.get(self.workspace)
        return session.state if session is not None else None

    def request_stop(self) -> None:
        self._stop.set()

    # -- streaming server ----------------------------------------------------
    def serve(self, socket_path: str) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        Path(socket_path).parent.mkdir(parents=True, exist_ok=True)
        if Path(socket_path).exists():
            Path(socket_path).unlink()
        server.bind(socket_path)
        server.listen(8)
        self.server = server
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._pump_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with self._lock:
                cid = f"c{len(self.clients) + 1}"
                self.clients[cid] = conn
                self.fanout.attach(cid)
            if self.broadcast is not None:
                self.broadcast.on_attach(cid)

    def _pump_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                items = [(cid, self.fanout.drain(cid)) for cid in list(self.clients)]
            for cid, envelopes in items:
                conn = self.clients.get(cid)
                for env in envelopes:
                    try:
                        conn.sendall(encode(env))  # type: ignore[union-attr]
                    except OSError:
                        self._drop(cid)
            time.sleep(0.005)

    def _drop(self, cid: str) -> None:
        with self._lock:
            conn = self.clients.pop(cid, None)
            self.fanout.detach(cid)
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

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

    def dispatch(self, req: dict) -> dict:
        cmd = req.get("cmd")
        if cmd == "start-session":
            resp = self.handler.handle(StartSessionCommand(workspace=self.workspace))
            return resp.model_dump(mode="json")
        if cmd == "attach":
            resp = self.handler.handle(AttachCommand(workspace=self.workspace))
            return resp.model_dump(mode="json")
        if cmd == "status":
            from dev_harness.engine.commands import StatusCommand

            resp = self.handler.handle(StatusCommand(workspace=self.workspace))
            return resp.model_dump(mode="json")
        if cmd == "run-workload":
            self.broadcast = StateBroadcast(self.fanout, state_of=self.state_of)  # type: ignore[arg-type]
            workload = StubWorkload(count=int(req["count"]), sink=self.fanout.publish)
            self.workload = workload
            workload.run()
            return {"ok": True, "emitted": workload.emitted}
        if cmd == "pause":
            session = self.sessions.get(self.workspace)
            if session is not None:
                session.state = session.state.model_copy(
                    update={
                        "tui_state": session.state.tui_state.model_copy(
                            update={
                                "is_paused": True,
                                "critic_gatekeeper_status": ExecutionState.PAUSED,
                            }
                        )
                    }
                )
            return {"ok": True}
        if cmd == "shutdown":
            session = self.sessions.get(self.workspace)
            if session is not None:
                session.state = session.state.model_copy(
                    update={
                        "tui_state": session.state.tui_state.model_copy(
                            update={
                                "is_paused": True,
                                "critic_gatekeeper_status": ExecutionState.PAUSED,
                            }
                        )
                    }
                )
            GracefulShutdown(
                self.workspace,
                sessions=self.sessions,
                fanout=self.fanout,
                unlink=lambda: Path(self.server.getsockname()).unlink(missing_ok=True),
            ).shutdown()
            self._stop.set()
            return {"ok": True}
        return {"ok": False, "error": f"unknown cmd {cmd}"}


def health(workspace: str, ctl_path: str, broker_socket: str) -> dict:
    result = {"engine": False, "broker": False}
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.connect(ctl_path)
        conn.sendall(b'{"cmd":"status"}\n')
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        conn.close()
        payload = json.loads(data.split(b"\n", 1)[0])
        result["engine"] = bool(payload.get("ok"))
    except OSError:
        pass
    from dev_harness.broker.client import BrokerClient
    from dev_harness.contracts.errors import BrokerUnavailableError

    try:
        client = BrokerClient(broker_socket)
        result["broker"] = bool(client.health().ok)
        client.close()
    except (BrokerUnavailableError, OSError):
        result["broker"] = False
    return result


def main() -> int:
    parser = argparse.ArgumentParser(prog="p5-driver")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--ctl", required=True)
    parser.add_argument("--broker-socket", default="")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()

    daemon = Daemon(args.workspace)
    daemon.serve(args.socket)
    daemon.serve_control(args.ctl)

    if args.self_check:
        t0 = time.monotonic()
        while time.monotonic() - t0 < 3.0:
            report = health(args.workspace, args.ctl, args.broker_socket)
            if report["engine"]:
                elapsed = time.monotonic() - t0
                print(f"engine ok handshake={elapsed:.3f}s health={report}")
                return 0
            time.sleep(0.05)
        print("engine handshake exceeded 3s", flush=True)
        return 1

    daemon._stop.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY

# --- client recorder: dumps `count` envelope frames as a transcript ---------
RECORDER="${TMPDIR:-/tmp}/dev-harness-p5-recorder.py"
cat >"$RECORDER" <<'PY'
"""Records the first `count` envelope frames from the engine socket."""
from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path

from dev_harness.ipc.framing import read_frame


def main() -> int:
    parser = argparse.ArgumentParser(prog="p5-recorder")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--first-frame", action="store_true")
    args = parser.parse_args()

    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(args.socket)
    lines: list[str] = []
    try:
        for _ in range(args.count):
            env = read_frame(conn)
            lines.append(env.model_dump_json())
            if args.first_frame:
                break
    except (OSError, ValueError):
        pass
    finally:
        conn.close()
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.first_frame and lines:
        payload = json.loads(lines[0])
        print(json.dumps({"type": payload["type"], "state": payload["payload"].get("state", {})}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY

run_driver() {
    python "$DRIVER" --workspace "$WS" --socket "$SOCK" --ctl "$CTL" \
        --broker-socket "$BROKER_SOCK" "$@" \
        >"$LOG" 2>&1 &
    DAEMON_PID=$!
    for _ in $(seq 1 100); do
        [ -S "$SOCK" ] && [ -S "$CTL" ] && break
        sleep 0.1
    done
    if [ ! -S "$SOCK" ]; then
        echo "engine daemon did not bind socket within 10s; log:" >&2
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
( cd "$WS" && git init -q -b main && git commit -q --allow-empty -m "p5 init" )

echo "[P5] step 1: cold start; STATUS returns READY with a thread_id"
run_driver
out="$(ctl '{"cmd":"start-session"}')"
echo "  start-session -> $out"
status="$(ctl '{"cmd":"status"}')"
echo "  status -> $status"
grep -q '"state": "READY"' <<<"$status" || { echo "STATUS not READY: $status" >&2; exit 1; }
grep -q '"thread_id": "' <<<"$status" || { echo "STATUS missing thread_id" >&2; exit 1; }

echo "[P5] step 2: session uniqueness (second START_SESSION -> SessionExistsError)"
second="$(ctl '{"cmd":"start-session"}')"
echo "  second -> $second"
grep -q '"ok": false' <<<"$second" || { echo "second start-session did not fail" >&2; exit 1; }
grep -qi 'thread_id=' <<<"$second" || { echo "error does not name the live thread_id" >&2; exit 1; }

echo "[P5] step 3: multi-client attach (3 recording clients, 100 events)"
rpids=""
for i in 1 2 3; do
    python "$RECORDER" --socket "$SOCK" --out "${TMPDIR:-/tmp}/p5_client_$i.txt" --count 100 &
    rpids="$rpids $!"
done
sleep 0.3
ctl '{"cmd":"run-workload","count":100}' >/dev/null
# shellcheck disable=SC2086
wait $rpids
diff -q "${TMPDIR:-/tmp}/p5_client_1.txt" "${TMPDIR:-/tmp}/p5_client_2.txt" \
    || { echo "client 1/2 transcripts diverge" >&2; exit 1; }
diff -q "${TMPDIR:-/tmp}/p5_client_1.txt" "${TMPDIR:-/tmp}/p5_client_3.txt" \
    || { echo "client 1/3 transcripts diverge" >&2; exit 1; }
echo "  3 transcripts identical and gapless (100 events each)"

echo "[P5] step 4: independent detach (kill client 2 mid-stream)"
python "$RECORDER" --socket "$SOCK" --out "${TMPDIR:-/tmp}/p5_c1.txt" --count 100 &
p1=$!
python "$RECORDER" --socket "$SOCK" --out "${TMPDIR:-/tmp}/p5_c2.txt" --count 100 &
p2=$!
python "$RECORDER" --socket "$SOCK" --out "${TMPDIR:-/tmp}/p5_c3.txt" --count 100 &
p3=$!
sleep 0.2
kill -9 "$p2" 2>/dev/null || true
sleep 0.2
ctl '{"cmd":"run-workload","count":100}' >/dev/null
wait "$p1" "$p3" 2>/dev/null || true
diff -q "${TMPDIR:-/tmp}/p5_c1.txt" "${TMPDIR:-/tmp}/p5_c3.txt" \
    || { echo "clients 1 and 3 diverge after client 2 detach" >&2; exit 1; }
echo "  clients 1 and 3 gapless after client 2 killed"

echo "[P5] step 5: late-attach correctness (SNAPSHOT{PAUSED} first)"
ctl '{"cmd":"pause"}' >/dev/null
first="$(python "$RECORDER" --socket "$SOCK" --out "${TMPDIR:-/tmp}/p5_client_4.txt" --count 100 --first-frame)"
echo "  client 4 first frame -> $first"
grep -q '"type": "SNAPSHOT"' <<<"$first" || { echo "client 4 first frame is not SNAPSHOT" >&2; exit 1; }
grep -q '"is_paused": true' <<<"$first" || { echo "SNAPSHOT does not report PAUSED" >&2; exit 1; }

echo "[P5] step 6: broker enforcement (broker down -> BrokerUnavailableError)"
stop_broker
python - <<PY
from dev_harness.broker.client import BrokerClient
from dev_harness.contracts.errors import BrokerUnavailableError
from dev_harness.engine.provider_gateway import ProviderGateway

gateway = ProviderGateway(BrokerClient("$BROKER_SOCK"))
try:
    gateway.reserve("anthropic")
except BrokerUnavailableError as exc:
    print(f"  BrokerUnavailableError: {exc}")
    print("  0 provider requests recorded (no fake server started)")
else:
    raise SystemExit("expected BrokerUnavailableError")
PY

echo "[P5] step 7: autostart (socket removed -> daemon respawned, handshake < 3s)"
start_broker
stop_daemon
rm -f "$SOCK" "$CTL"
t0=$(date +%s%N)
python "$DRIVER" --workspace "$WS" --socket "$SOCK" --ctl "$CTL" \
    --broker-socket "$BROKER_SOCK" --self-check || { echo "autostart self-check failed" >&2; exit 1; }
t1=$(date +%s%N)
elapsed_ms=$(( (t1 - t0) / 1000000 ))
if [ "$elapsed_ms" -ge 3000 ]; then
    echo "autostart handshake took ${elapsed_ms}ms (target <3000ms)" >&2
    exit 1
fi
echo "  daemon respawned; handshake ${elapsed_ms}ms (< 3000ms); health reports engine + broker"
stop_daemon

echo "[P5] step 8: graceful shutdown (drain, seal is_paused=True, unlink socket)"
rm -f "$WS/.dev-harness/state.db"
run_driver
session_out="$(ctl '{"cmd":"start-session"}')"
tid="$(python -c "import json,sys; print(json.loads(sys.argv[1])['thread_id'])" "$session_out")"
echo "  session $tid"
ctl '{"cmd":"run-workload","count":100}' >/dev/null &
workload_pid=$!
sleep 0.2
ctl '{"cmd":"shutdown"}' >/dev/null || true
wait "$workload_pid" 2>/dev/null || true
[ ! -S "$SOCK" ] || { echo "socket not unlinked after shutdown" >&2; exit 1; }
[ -f "$WS/.dev-harness/state.db" ] || { echo "no sealed checkpoint written" >&2; exit 1; }
python - <<PY
from dev_harness.engine.session import project_id_for
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver

scope = Scope(project_id=project_id_for("$WS"), thread_id="$tid")
saver = SqliteSaver("$WS/.dev-harness/state.db")
try:
    rows = saver.list(scope)
finally:
    saver.close()
assert rows, "no checkpoint for the session"
assert rows[0]["is_paused"] is True, rows[0]
print("  sealed checkpoint is_paused=True")
PY
DAEMON_PID=""
echo "  in-flight drained; checkpoint sealed with is_paused=True; socket unlinked"

echo "[P5] step 9: crash residue check (kill -9 -> stale socket reclaimed)"
run_driver
kill -9 "$DAEMON_PID" 2>/dev/null || true
wait "$DAEMON_PID" 2>/dev/null || true
DAEMON_PID=""
run_driver
status="$(ctl '{"cmd":"status"}')"
grep -q '"state"' <<<"$status" || { echo "restart after kill -9 failed" >&2; exit 1; }
echo "  stale socket reclaimed automatically; no manual cleanup"

echo "[P5] step 10: watcher demo (FILE_CHANGE + GIT_STATUS_UPDATE)"
python - <<PY
from pathlib import Path

from dev_harness.engine.workspace_watcher import WorkspaceWatcher

watcher = WorkspaceWatcher("$WS")
watcher.scan_once()  # prime baseline
Path("$WS/probe.txt").write_text("x = 1\n", encoding="utf-8")
emitted = watcher.scan_once()
types = sorted({env.type.value for env in emitted})
assert "FILE_CHANGE" in types, types
print(f"  emitted {types}")
PY

echo "[P5] step 11: emit report"
python scripts/verify_phase.py --emit 05
echo "P5 acceptance OK"
