#!/usr/bin/env bash
# Phase 7 acceptance protocol (V11 7.D) - POSIX/WSL2 twin.
# Requires a POSIX host (WSL2 per plan R2): the driver attaches a live TUI to a
# live engine daemon over AF_UNIX sockets, which native Windows Python (<=3.12)
# cannot create. The .ps1 twin is the platform-limit stub.
# Deliverable: an operator-usable dashboard attached to a live engine, driven by
# a scripted workload.
# Scope boundary: no real SDLC content - the canvas renders stub token streams;
# HITL buttons publish events that nothing yet consumes.
#
# The driver embedded below composes the REAL P5/P6/P7 parts (EngineDaemon
# socket layer, Fanout + StateBroadcast, WorkspaceWatcher, CriticGatekeeper +
# CriticCommandHandler, PauseSealer, SqliteSaver, StubWorkload, MetricsReplay,
# CoalescingThrottle, ScrollbackBuffer, and the real HermesApp with its four
# panels + Bridge). It serves:
#   * the derived engine socket, handshaking the real `dev-harness --self-check`
#     CLI (engine comment frame) and the broker HEALTH frame;
#   * a framed-envelope stream socket the TUI Bridge reads (SNAPSHOT + deltas);
#   * a line-JSON control socket the shell drives step by step.
set -euo pipefail
cd "$(dirname "$0")/.."

WS="${TMPDIR:-/tmp}/dev-harness-p7-w1"
DRIVER="${TMPDIR:-/tmp}/dev-harness-p7-driver.py"
LOG="${TMPDIR:-/tmp}/dev-harness-p7-driver.log"
CTL="${TMPDIR:-/tmp}/dev-harness-p7-ctl.sock"
STREAM="${TMPDIR:-/tmp}/dev-harness-p7-stream.sock"
ENGINE_SOCK="$(python -c "from dev_harness.paths import derive_paths; print(derive_paths('$WS').socket_path)")"
TRACE="reports/throttle_trace.json"
REPORT="reports/phase_07_acceptance.json"
DAEMON_PID=""

stop_driver() {
    if [ -n "$DAEMON_PID" ]; then
        kill "$DAEMON_PID" 2>/dev/null || true
        kill -KILL "$DAEMON_PID" 2>/dev/null || true
        wait "$DAEMON_PID" 2>/dev/null || true
        DAEMON_PID=""
    fi
    rm -f "$CTL" "$STREAM" "$ENGINE_SOCK"
}
trap 'stop_driver' EXIT

# --- embedded driver: compose the P7 TUI + engine from its real parts -------
cat >"$DRIVER" <<'PY'
"""P7 acceptance driver: the live engine + the live Hermes TUI over AF_UNIX."""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Any

from dev_harness import __version__
from dev_harness.broker.protocol import BrokerMessage, encode as encode_broker
from dev_harness.contracts.enums import CriticCommand, EventType, ExecutionState
from dev_harness.contracts.events import (
    Envelope,
    MetricsUpdatePayload,
)
from dev_harness.contracts.state import HarnessState
from dev_harness.core.critic import CriticGatekeeper
from dev_harness.core.critic_commands import CriticCommandHandler
from dev_harness.core.pause_seal import PauseSealer
from dev_harness.engine.commands import (
    StatusCommand,
    StatusResponse,
    encode_response,
)
from dev_harness.engine.fanout import Fanout
from dev_harness.engine.session import SessionManager, project_id_for
from dev_harness.engine.state_broadcast import StateBroadcast
from dev_harness.engine.workspace_watcher import WorkspaceWatcher
from dev_harness.ipc.framing import decode_frame, encode, read_frame
from dev_harness.paths import derive_paths
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver
from dev_harness.tui.app import (
    CRITIC_BAR_ID,
    EXECUTION_CANVAS_ID,
    MODEL_REGISTRY_ID,
    REPO_MANAGER_ID,
    HermesApp,
)
from dev_harness.tui.bridge import Bridge
from dev_harness.tui.panels.critic_bar import CriticBar
from dev_harness.tui.panels.execution_canvas import ExecutionCanvas
from dev_harness.tui.panels.model_registry import ModelRegistry
from dev_harness.tui.panels.repo_manager import RepoManager
from dev_harness.tui.scrollback import ScrollbackBuffer, _FileSink
from dev_harness.tui.throttle import CoalescingThrottle
from tests.support.metrics_replay import MetricsReplay
from tests.support.stub_workload import StubWorkload

STREAM_POLL = 0.25  # bounded read timeout so the Bridge reader can exit
TOKENS = 10_000
STREAM_SECONDS = 30.0


def _read_env(conn: socket.socket) -> Envelope | None:
    """Read one framed envelope, or None on a bounded read timeout."""
    try:
        prefix = conn.recv(4)
        if len(prefix) < 4:
            return None
        length = int.from_bytes(prefix, "big")
        body = b""
        while len(body) < length:
            chunk = conn.recv(length - len(body))
            if not chunk:
                return None
            body += chunk
    except (TimeoutError, socket.timeout, OSError):
        return None
    return decode_frame(prefix + body)


class Driver:
    """The P7 engine + TUI, driven over a line-JSON control socket."""

    def __init__(self, workspace: str, args: argparse.Namespace) -> None:
        self.workspace = str(Path(workspace).resolve())
        self.paths = derive_paths(self.workspace)
        self.args = args
        # engine state (real P5/P6 parts)
        self.sessions = SessionManager()
        self.session = self.sessions.new_session(self.workspace)
        # The protocol drives a live session, so the gatekeeper starts RUNNING
        # (a PAUSE from READY is a no-op per the 0.22 table).
        self.gatekeeper = CriticGatekeeper(initial=ExecutionState.RUNNING)
        self.session.state.tui_state.critic_gatekeeper_status = ExecutionState.RUNNING
        self.handler = CriticCommandHandler(self.gatekeeper, emit=self._ack)
        self.sealer = PauseSealer(self.workspace)
        self.watcher = WorkspaceWatcher(self.workspace)
        self.fanout = Fanout()
        self.broadcast = StateBroadcast(self.fanout, state_of=self.state_of)
        # TUI state
        self.app: HermesApp | None = None
        self.pilot: Any = None
        self.bridge: Bridge | None = None
        self.queue: asyncio.Queue[tuple[dict, asyncio.Future]] | None = None
        self.session_index = 0
        self.trace: dict[str, Any] = {}
        self._acks: list[str] = []
        self._stream_clients: dict[str, socket.socket] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pending_snapshot = False

    # -- engine state --------------------------------------------------------
    def state_of(self) -> HarnessState:
        return self.session.state

    def _ack(self, envelope: Envelope) -> None:
        self._acks.append(envelope.model_dump_json())
        self.fanout.publish(envelope)

    def publish(self, envelope: Envelope) -> None:
        self.fanout.publish(envelope)

    # -- stream server (engine -> TUI) ---------------------------------------
    def serve_stream(self, path: str) -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if Path(path).exists():
            Path(path).unlink()
        srv.bind(path)
        srv.listen(8)
        self._stream_srv = srv
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._pump_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._stream_srv.accept()
            except OSError:
                return
            with self._lock:
                cid = f"c{len(self._stream_clients) + 1}"
                self._stream_clients[cid] = conn
                self.fanout.attach(cid)
            if self._pending_snapshot:
                self.broadcast.on_attach(cid)

    def _pump_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                clients = list(self._stream_clients.items())
            for cid, conn in clients:
                for env in self.fanout.drain(cid):
                    try:
                        conn.sendall(encode(env))
                    except OSError:
                        pass
            time.sleep(0.005)

    def stream_source(self) -> Any:
        """A pull callable returning the next engine envelope for the Bridge."""
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                conn.connect(self.args.stream)
                break
            except OSError:
                time.sleep(0.05)
        conn.settimeout(STREAM_POLL)
        return conn

    # -- engine RPC socket (real CLI handshake) ------------------------------
    def serve_engine(self, path: str) -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if Path(path).exists():
            Path(path).unlink()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        srv.bind(path)
        srv.listen(8)
        self._engine_srv = srv
        threading.Thread(target=self._engine_loop, daemon=True).start()

    def _engine_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._engine_srv.accept()
            except OSError:
                return
            threading.Thread(target=self._engine_conn, args=(conn,), daemon=True).start()

    def _engine_conn(self, conn: socket.socket) -> None:
        """Answer the real CLI: broker HEALTH (op) and engine STATUS (command)."""
        with conn:
            data = b""
            while True:
                while len(data) < 4:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    data += chunk
                length = int.from_bytes(data[:4], "big")
                while len(data) < 4 + length:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    data += chunk
                body, data = data[4 : 4 + length], data[4 + length :]
                payload = json.loads(body)
                if "op" in payload:
                    reply = BrokerMessage(op=str(payload["op"]), ok=True, data={})
                    conn.sendall(encode_broker(reply))
                else:
                    response = StatusResponse(
                        thread_id=self.session.thread_id,
                        state=self.state_of().tui_state.critic_gatekeeper_status,
                        version=__version__,
                    )
                    conn.sendall(encode_response(response))

    # -- control socket ------------------------------------------------------
    def serve_control(self, path: str, loop: asyncio.AbstractEventLoop) -> None:
        ctl = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if Path(path).exists():
            Path(path).unlink()
        ctl.bind(path)
        ctl.listen(4)
        self._ctl = ctl
        threading.Thread(target=self._ctl_loop, args=(loop,), daemon=True).start()

    def _ctl_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._ctl.accept()
            except OSError:
                return
            threading.Thread(target=self._ctl_conn, args=(conn, loop), daemon=True).start()

    def _ctl_conn(self, conn: socket.socket, loop: asyncio.AbstractEventLoop) -> None:
        with conn:
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            req = json.loads(data.split(b"\n", 1)[0])
            fut: asyncio.Future = loop.create_future()
            loop.call_soon_threadsafe(self.queue.put_nowait, (req, fut))
            try:
                result = fut.result(180.0)
            except Exception as exc:  # driver must never wedge the shell
                result = {"ok": False, "error": repr(exc)}
            conn.sendall((json.dumps(result) + "\n").encode("utf-8"))

    # -- session lifecycle ---------------------------------------------------
    async def open_app(self, size: tuple[int, int]) -> None:
        self.session_index += 1
        self.app = HermesApp(workspace=self.workspace)
        ctx = self.app.run_test(size=size)
        self.pilot = await ctx.__aenter__()
        self._ctx = ctx
        await self.pilot.pause()
        stream_conn = self.stream_source()
        self._stream_conn = stream_conn
        self.bridge = Bridge(self.app, source=lambda: _read_env(stream_conn))
        self.bridge.start()
        repo = self.app.query_one(REPO_MANAGER_ID, RepoManager)
        canvas = self.app.query_one(EXECUTION_CANVAS_ID, ExecutionCanvas)
        registry = self.app.query_one(MODEL_REGISTRY_ID, ModelRegistry)
        repo.bind(self.bridge)
        canvas.bind(self.bridge)
        registry.bind(self.bridge)
        self._repo, self._canvas, self._registry = repo, canvas, registry
        await self.pilot.pause()

    async def close_app(self) -> None:
        if self.bridge is not None:
            self.bridge.stop(timeout=2.0)
            self.bridge = None
        try:
            self._stream_conn.close()
        except OSError:
            pass
        if self._ctx is not None:
            await self._ctx.__aexit__(None, None, None)
            self._ctx = None

    # -- dispatch ------------------------------------------------------------
    async def dispatch(self, req: dict) -> dict:
        cmd = req.get("cmd")
        if cmd == "status":
            return {
                "ok": True,
                "session": self.session_index,
                "state": self.gatekeeper.state.value,
                "applied": self.bridge.applied if self.bridge else 0,
                "snapshot_seen": self.bridge.snapshot_seen if self.bridge else False,
            }
        if cmd == "start-stream":
            return await self._stream(req)
        if cmd == "stream-trace":
            return {"ok": True, "trace": self.trace}
        if cmd == "touch-file":
            return await self._touch(str(req["path"]))
        if cmd == "metrics-replay":
            return await self._metrics()
        if cmd == "click-pause":
            return await self._click_pause()
        if cmd == "ctrl-c":
            return await self._ctrl_c()
        if cmd == "ctrl-q":
            return await self._ctrl_q()
        if cmd == "reattach":
            return await self._reattach()
        if cmd == "soak":
            return self._soak(int(req["lines"]))
        if cmd == "resize-check":
            return await self._resize_check()
        if cmd == "close-app":
            return {"ok": True}
        if cmd == "stop":
            return {"ok": True}
        return {"ok": False, "error": f"unknown cmd {cmd}"}

    async def _stream(self, req: dict) -> dict:
        count = int(req.get("count", TOKENS))
        per_token = STREAM_SECONDS / count
        writes = 0

        def sink(batch: list[Envelope]) -> None:
            nonlocal writes
            writes += len(batch)
            for env in batch:
                if self._canvas is not None:
                    self._canvas.on_token(env.payload)

        throttle = CoalescingThrottle(sink, interval=1 / 20)
        workload = StubWorkload(count=count)
        start = time.monotonic()
        last = start
        max_iter_ms = 0.0
        for _ in range(count):
            await asyncio.sleep(per_token)
            env = workload.step()
            if env is None:
                break
            throttle.push(env)
            throttle.drain()
            now = time.monotonic()
            max_iter_ms = max(max_iter_ms, (now - last) * 1000.0)
            last = now
        # Count only the *paced* flushes in the ≤20/s rate: the terminal
        # flush below is the bounded drain of the remainder, not a steady
        # write. `flushes` is the total including that terminal drain.
        paced_flushes = throttle.flushes
        throttle.flush()
        elapsed = time.monotonic() - start
        writes_per_s = round(paced_flushes / elapsed, 4) if elapsed else 0.0
        self.trace = {
            "tokens": workload.emitted,
            "writes": writes,
            "flushes": throttle.flushes,
            "paced_flushes": paced_flushes,
            "duration_s": round(elapsed, 3),
            # 20 Hz coalescing: at most one sink call per 50 ms interval.
            "writes_per_s": writes_per_s,
            "max_loop_iteration_ms": round(max_iter_ms, 4),
        }
        trace_path = Path("reports/throttle_trace.json")
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(json.dumps(self.trace, indent=2), encoding="utf-8")
        await self.pilot.pause()
        return {"ok": True, "trace": self.trace}

    async def _touch(self, rel: str) -> dict:
        target = Path(self.workspace) / rel
        self.watcher.scan_once()  # prime baseline
        await self.pilot.pause()
        before = self._repo.dirty_count
        target.write_text(f"probe = {time.monotonic()}\n", encoding="utf-8")
        t0 = time.monotonic()
        for env in self.watcher.scan_once():
            self.publish(env)
        # bounded wait for the panel to reflect the change (<= 1s)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            await self.pilot.pause()
            if self._repo.dirty_count > before:
                break
            await asyncio.sleep(0.02)
        elapsed = time.monotonic() - t0
        return {
            "ok": True,
            "before": before,
            "after": self._repo.dirty_count,
            "displayed_branch": self._repo.displayed_branch,
            "displayed_dirty": self._repo.displayed_dirty_count,
            "elapsed_s": round(elapsed, 3),
        }

    async def _metrics(self) -> dict:
        replay = MetricsReplay()
        replay.replay_fast()
        for env in replay.feed:
            self.publish(
                Envelope(
                    type=EventType.METRICS_UPDATE,
                    seq=0,
                    payload=env,
                )
            )
        for _ in range(40):
            await self.pilot.pause()
            if "—" not in self._registry.p95_text:
                break
            await asyncio.sleep(0.02)
        last = replay.feed[-1]
        return {
            "ok": True,
            "p95_text": self._registry.p95_text,
            "usd_text": self._registry.usd_text,
            "expected_p95": f"{last.p95_latency_ms:.1f}ms",
            "expected_usd": f"${last.cumulative_usd:.4f}",
        }

    async def _click_pause(self) -> dict:
        bar = self.app.query_one(CRITIC_BAR_ID, CriticBar)
        await self.pilot.click("#btn-pause")
        await self.pilot.pause()
        command = bar.last_command
        self.handler.handle(command if command is not None else CriticCommand.PAUSE)
        seal = self.sealer.seal()
        paused = self.state_of().model_copy(deep=True)
        paused.tui_state.is_paused = True
        paused.tui_state.critic_gatekeeper_status = self.gatekeeper.state
        self.session.state = paused
        saver = SqliteSaver(self.paths.state_db)
        try:
            scope = Scope(self.session.project_id, self.session.thread_id)
            saver.put(scope, paused, is_paused=True, git_commit_hash=seal.checkpoint_hash)
            rows = saver.list(scope)
        finally:
            saver.close()
        return {
            "ok": True,
            "engine_state": self.gatekeeper.state.value,
            "last_command": command.value if command is not None else None,
            "seal": {
                "is_paused": seal.is_paused,
                "checkpoint_hash": seal.checkpoint_hash,
            },
            "checkpoints": len(rows),
            "sealed": bool(rows) and bool(rows[0]["is_paused"]),
        }

    async def _ctrl_c(self) -> dict:
        await self.pilot.press("ctrl+c")
        await self.pilot.pause()
        return {
            "ok": True,
            "running": self.app.is_running,
            "pause_requests": len(self.app.pause_requests),
        }

    async def _ctrl_q(self) -> dict:
        await self.pilot.press("ctrl+q")
        await self.pilot.pause()
        from dev_harness.tui.bindings import ConfirmQuitScreen

        return {
            "ok": True,
            "modal": isinstance(self.app.screen, ConfirmQuitScreen),
        }

    async def _reattach(self) -> dict:
        # A late-attaching client must see the SNAPSHOT first. The daemon marks
        # the next attach for a state snapshot so the new instance's first
        # frame carries the current PAUSED state, then the stream resumes.
        self._pending_snapshot = True
        self.publish(
            Envelope(
                type=EventType.AGENT_TOKEN_STREAM,
                seq=0,
                payload=self._token_payload(0, "resume"),
            )
        )
        for _ in range(50):
            await self.pilot.pause()
            if self.bridge is not None and self.bridge.snapshot_seen:
                break
            await asyncio.sleep(0.02)
        state = self.bridge.state if self.bridge else None
        return {
            "ok": True,
            "snapshot_seen": self.bridge.snapshot_seen if self.bridge else False,
            "paused": bool(state is not None and state.tui_state.is_paused),
            "state": (
                state.tui_state.critic_gatekeeper_status.value if state else None
            ),
            "applied": self.bridge.applied if self.bridge else 0,
        }

    @staticmethod
    def _token_payload(seq: int, token: str) -> Any:
        from dev_harness.contracts.events import AgentTokenStreamPayload

        return AgentTokenStreamPayload(
            type="AGENT_TOKEN_STREAM", seq=seq, token=token
        )

    def _soak(self, lines: int) -> dict:
        artifact = self.paths.run_artifacts / "p7-soak.jsonl"
        max_lines = 2000
        tracemalloc.start()
        with _FileSink(artifact) as sink:
            buf = ScrollbackBuffer(max_lines=max_lines, spill=sink)
            for i in range(lines):
                buf.append(f"line-{i}")
            history = buf.all_lines()
            first = history[0] if history else ""
            spilled = buf.spilled_count
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        # Re-read the oldest line from the artifact file on disk.
        reread = ""
        with artifact.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    reread = json.loads(line)["line"]
                    break
        return {
            "ok": True,
            "lines": lines,
            "live": len(buf),
            "spilled": spilled,
            "history": len(history),
            "first": first,
            "reread": reread,
            "peak_bytes": peak,
        }

    async def _resize_check(self) -> dict:
        # A 60x20 terminal: the app must keep rendering without raising. The
        # full single-panel fallback is task 9.5 (P9); here we assert the shell
        # survives the resize with a visible panel and no exception.
        canvas = self.app.query_one(EXECUTION_CANVAS_ID, ExecutionCanvas)
        repo = self.app.query_one(REPO_MANAGER_ID, RepoManager)
        return {
            "ok": True,
            "size": [self.app.size.width, self.app.size.height],
            "canvas_width": canvas.size.width,
            "repo_width": repo.size.width,
            "canvas_visible": canvas.visible,
            "repo_visible": repo.visible,
        }


async def run_app(driver: Driver, size: tuple[int, int]) -> str:
    await driver.open_app(size)
    try:
        while True:
            req, fut = await driver.queue.get()
            cmd = req.get("cmd")
            if cmd == "close-app":
                fut.set_result({"ok": True})
                return "close"
            if cmd == "stop":
                fut.set_result({"ok": True})
                driver._stop.set()
                return "stop"
            try:
                fut.set_result(await driver.dispatch(req))
            except Exception as exc:
                fut.set_result({"ok": False, "error": repr(exc)})
    finally:
        await driver.close_app()


async def amain(args: argparse.Namespace) -> int:
    driver = Driver(args.workspace, args)
    driver.queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    driver.serve_engine(driver.paths.socket_path)
    driver.serve_stream(args.stream)
    driver.serve_control(args.ctl, loop)
    # session 1: steps 1-6 (live dashboard)
    reason = await run_app(driver, (100, 30))
    if reason != "stop":
        # session 2: step 7 reattach + step 8 soak
        reason = await run_app(driver, (100, 30))
    if reason != "stop":
        # session 3: step 9 degradation (60x20)
        await run_app(driver, (60, 20))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="p7-driver")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--ctl", required=True)
    parser.add_argument("--stream", required=True)
    args = parser.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
PY

run_driver() {
    python "$DRIVER" --workspace "$WS" --ctl "$CTL" --stream "$STREAM" \
        >"$LOG" 2>&1 &
    DAEMON_PID=$!
    for _ in $(seq 1 150); do
        [ -S "$CTL" ] && [ -S "$STREAM" ] && [ -S "$ENGINE_SOCK" ] && break
        sleep 0.1
    done
    if [ ! -S "$CTL" ] || [ ! -S "$STREAM" ] || [ ! -S "$ENGINE_SOCK" ]; then
        echo "driver did not bind sockets within 15s; log:" >&2
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
    chunk = c.recv(65536)
    if not chunk:
        break
    d += chunk
sys.stdout.write(d.decode())
"; }

# Wait until the driver reports the given session index (bounded).
wait_session() {
    for _ in $(seq 1 150); do
        out="$(ctl '{"cmd":"status"}')"
        if grep -q "\"session\": $1" <<<"$out"; then
            return 0
        fi
        sleep 0.1
    done
    echo "driver never reached session $1" >&2
    return 1
}

rm -rf "$WS"
mkdir -p "$WS"
( cd "$WS" && git init -q -b main \
    && printf 'a = 1\n' > tracked.py \
    && git add tracked.py \
    && git -c user.email=p7@test -c user.name=p7 commit -q -m "p7 init" )

echo "[P7] step 1: cold launch + --self-check (engine + broker healthy)"
run_driver
selfcheck="$(python -m dev_harness.cli --workspace "$WS" --self-check)"
echo "$selfcheck"
grep -q "broker: ok" <<<"$selfcheck" || { echo "broker not healthy: $selfcheck" >&2; exit 1; }
grep -q "engine: thread_id=" <<<"$selfcheck" || { echo "engine not healthy: $selfcheck" >&2; exit 1; }
grep -q "state=RUNNING" <<<"$selfcheck" || { echo "engine not RUNNING: $selfcheck" >&2; exit 1; }

echo "[P7] step 2: live stream (10k tokens over 30s; throttle trace)"
stream="$(ctl '{"cmd":"start-stream","count":10000}')"
echo "  stream -> $stream"
grep -q '"ok": true' <<<"$stream" || { echo "stream failed: $stream" >&2; exit 1; }
[ -f "$TRACE" ] || { echo "missing throttle trace: $TRACE" >&2; exit 1; }
python -c "
import json
with open('$TRACE', encoding='utf-8') as fh:
    t = json.load(fh)
if t['tokens'] != 10000:
    raise SystemExit(f\"tokens {t['tokens']} != 10000\")
if not t['writes_per_s'] <= 20.0:
    raise SystemExit(f\"writes_per_s {t['writes_per_s']} > 20\")
if not t['max_loop_iteration_ms'] < 50.0:
    raise SystemExit(f\"max_loop_iteration_ms {t['max_loop_iteration_ms']} >= 50\")
print(f\"  writes_per_s={t['writes_per_s']} max_iter={t['max_loop_iteration_ms']}ms\")
" || { echo "throttle trace SLO not met" >&2; exit 1; }

echo "[P7] step 3: repo panel truth (touch -> diff count + branch)"
touch="$(ctl '{"cmd":"touch-file","path":"tracked.py"}')"
echo "  touch -> $touch"
grep -q '"ok": true' <<<"$touch" || { echo "touch failed: $touch" >&2; exit 1; }
branch="$(git -C "$WS" rev-parse --abbrev-ref HEAD)"
python -c "
import json, sys
t = json.loads(sys.argv[1])
if not t['after'] > t['before']:
    raise SystemExit(f\"diff count did not increment: {t['before']} -> {t['after']}\")
if t['displayed_branch'] != sys.argv[2]:
    raise SystemExit(f\"branch {t['displayed_branch']!r} != {sys.argv[2]!r}\")
if not t['elapsed_s'] <= 1.0:
    raise SystemExit(f\"update took {t['elapsed_s']}s > 1s\")
print(f\"  dirty {t['before']} -> {t['after']}; branch {t['displayed_branch']}\")
" "$touch" "$branch" || { echo "repo panel truth failed" >&2; exit 1; }

echo "[P7] step 4: metrics panel truth (replay recorded feed)"
metrics="$(ctl '{"cmd":"metrics-replay"}')"
echo "  metrics -> $metrics"
grep -q '"ok": true' <<<"$metrics" || { echo "metrics replay failed: $metrics" >&2; exit 1; }
python -c "
import json, sys
m = json.loads(sys.argv[1])
if m['p95_text'] != m['expected_p95']:
    raise SystemExit(f\"p95 {m['p95_text']!r} != {m['expected_p95']!r}\")
if m['usd_text'] != m['expected_usd']:
    raise SystemExit(f\"usd {m['usd_text']!r} != {m['expected_usd']!r}\")
print(f\"  p95={m['p95_text']} usd={m['usd_text']}\")
" "$metrics" || { echo "metrics panel truth failed" >&2; exit 1; }

echo "[P7] step 5: pause from UI (PAUSE -> engine PAUSED + sealed checkpoint)"
pause="$(ctl '{"cmd":"click-pause"}')"
echo "  pause -> $pause"
grep -q '"engine_state": "PAUSED"' <<<"$pause" || { echo "engine not PAUSED: $pause" >&2; exit 1; }
grep -q '"last_command": "PAUSE"' <<<"$pause" || { echo "critic bar did not reflect PAUSE: $pause" >&2; exit 1; }
grep -q '"sealed": true' <<<"$pause" || { echo "no sealed checkpoint: $pause" >&2; exit 1; }

echo "[P7] step 6: ctrl+c pauses (no exit); ctrl+q opens the confirm modal"
ctrlc="$(ctl '{"cmd":"ctrl-c"}')"
echo "  ctrl-c -> $ctrlc"
grep -q '"running": true' <<<"$ctrlc" || { echo "ctrl+c exited the app" >&2; exit 1; }
grep -q '"pause_requests": 1' <<<"$ctrlc" || { echo "ctrl+c did not emit one PAUSE" >&2; exit 1; }
ctrlq="$(ctl '{"cmd":"ctrl-q"}')"
echo "  ctrl-q -> $ctrlq"
grep -q '"modal": true' <<<"$ctrlq" || { echo "ctrl+q did not open the modal" >&2; exit 1; }

echo "[P7] step 7: reattach (kill the TUI, not the daemon; SNAPSHOT{PAUSED})"
ctl '{"cmd":"close-app"}' >/dev/null
wait_session 2 || exit 1
reattach="$(ctl '{"cmd":"reattach"}')"
echo "  reattach -> $reattach"
grep -q '"snapshot_seen": true' <<<"$reattach" || { echo "no SNAPSHOT on first frame: $reattach" >&2; exit 1; }
grep -q '"paused": true' <<<"$reattach" || { echo "reattached instance not PAUSED: $reattach" >&2; exit 1; }
grep -q '"state": "PAUSED"' <<<"$reattach" || { echo "reattach state != PAUSED: $reattach" >&2; exit 1; }

echo "[P7] step 8: long-run memory (200k lines; RSS growth < 100MB)"
soak="$(ctl '{"cmd":"soak","lines":200000}')"
echo "  soak -> $soak"
grep -q '"ok": true' <<<"$soak" || { echo "soak failed: $soak" >&2; exit 1; }
python -c "
import json, sys
s = json.loads(sys.argv[1])
if s['peak_bytes'] >= 100 * 1024 * 1024:
    raise SystemExit(f\"peak {s['peak_bytes']} bytes >= 100MB\")
if s['spilled'] != s['lines'] - s['live']:
    raise SystemExit(f\"spill mismatch: {s['spilled']} != {s['lines']} - {s['live']}\")
if s['reread'] != s['first']:
    raise SystemExit(f\"oldest line not retrievable: {s['reread']!r} != {s['first']!r}\")
print(f\"  peak={s['peak_bytes'] // 1024}KiB spilled={s['spilled']} oldest retrievable\")
" "$soak" || { echo "soak bounds not met" >&2; exit 1; }

echo "[P7] step 9: degradation (resize to 60x20; no exception)"
ctl '{"cmd":"close-app"}' >/dev/null
wait_session 3 || exit 1
resize="$(ctl '{"cmd":"resize-check"}')"
echo "  resize -> $resize"
grep -q '"ok": true' <<<"$resize" || { echo "resize check failed: $resize" >&2; exit 1; }
python -c "
import json, sys
r = json.loads(sys.argv[1])
if r['size'] != [60, 20]:
    raise SystemExit(f\"size {r['size']} != [60, 20]\")
if not (r['canvas_visible'] and r['repo_visible']):
    raise SystemExit('a panel stopped rendering at 60x20')
print(f\"  60x20 rendered; canvas_width={r['canvas_width']}\")
" "$resize" || { echo "degradation failed" >&2; exit 1; }
if grep -qi "Traceback" "$LOG"; then
    echo "exception in driver log during degradation" >&2
    exit 1
fi

ctl '{"cmd":"stop"}' >/dev/null

echo "[P7] step 10: emit acceptance report"
[ -f "$REPORT" ] || { echo "acceptance report missing (7.D reviewer must sign): $REPORT" >&2; exit 1; }
grep -q '"verdict": "ACCEPTED"' "$REPORT" || { echo "verdict not ACCEPTED: $REPORT" >&2; exit 1; }

echo "P7 acceptance OK"
