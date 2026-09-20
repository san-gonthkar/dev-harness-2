"""dev-harness-broker daemon: host-scoped single-instance + socket (V11 4.8).

Per ADR-0002 the broker socket and single-instance lock are host-scoped
(~/.local/share/dev-harness/), NOT workspace-scoped: a workspace-scoped
broker cannot limit across projects — the entire point of the broker.

The daemon owns the token buckets, reservation store, local limiter, cost
governor, and kill-switch. It serves a length-prefixed JSON endpoint
(HEALTH, RESERVE, COMMIT, RELEASE, METRICS) and drains gracefully.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dev_harness.broker.backoff import Backoff
from dev_harness.broker.bucket import TokenBucket
from dev_harness.broker.cost import BudgetExceededError, CostGovernor
from dev_harness.broker.kill_switch import KillSwitch
from dev_harness.broker.local_limiter import LocalLimiter
from dev_harness.broker.policies import PolicyRegistry
from dev_harness.broker.protocol import BrokerMessage, encode, read_frame
from dev_harness.broker.reservation import ReservationStore
from dev_harness.config import HarnessConfig
from dev_harness.contracts.enums import ProviderId
from dev_harness.contracts.errors import HarnessError
from dev_harness.providers.registry import ModelRegistry

# Host-scoped locations (ADR-0002).
HOST_DATA_DIR = Path.home() / ".local" / "share" / "dev-harness"
DEFAULT_SOCKET_PATH = HOST_DATA_DIR / "broker.sock"
DEFAULT_LOCK_PATH = HOST_DATA_DIR / "broker.lock"

_AF_UNIX = getattr(socket, "AF_UNIX", 1)
_SOCK_STREAM = getattr(socket, "SOCK_STREAM", 1)


class AlreadyRunningError(HarnessError):
    """A second broker instance tried to start."""

    remediation = "The broker is already running; use the existing instance."


class BrokerUnavailableError(HarnessError):
    """The broker could not be reached (fail-closed client)."""

    remediation = "Start the broker daemon or set allow_unbrokered=true."


class BrokerDaemon:
    """The host-scoped rate-limit broker."""

    def __init__(
        self,
        config: HarnessConfig | None = None,
        *,
        socket_path: str | Path | None = None,
        lock_path: str | Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        socket_factory: Callable[[int, int], Any] | None = None,
    ) -> None:
        self.config = config or HarnessConfig()
        self.socket_path = Path(socket_path) if socket_path else DEFAULT_SOCKET_PATH
        self.lock_path = Path(lock_path) if lock_path else DEFAULT_LOCK_PATH
        self._clock = clock
        self._socket_factory = socket_factory or socket.socket
        self._policies = PolicyRegistry(self.config)
        self._registry = ModelRegistry(self.config)
        self._buckets: dict[ProviderId, TokenBucket] = {}
        self._limiters: dict[ProviderId, LocalLimiter] = {}
        self._reservations = ReservationStore(clock=clock)
        self._governor = CostGovernor(
            self._registry,
            budget_usd_per_run=self.config.budget_usd_per_run,
            budget_usd_per_day=self.config.budget_usd_per_day,
            clock=clock,
        )
        self._kill = KillSwitch(self._emit_interrupt)
        self._backoff = Backoff()
        self._lock_handle: Any = None
        self._server: Any = None
        self._running = False
        self._draining = False
        self._metrics: dict[str, float] = {
            "p50_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "tpm_burn": 0.0,
            "cumulative_usd": 0.0,
        }
        self._latencies: list[float] = []
        self._lat_lock = threading.Lock()
        self._tpm_burn = 0
        self._tpm_lock = threading.Lock()
        self._build_buckets()

    def _build_buckets(self) -> None:
        """Build a token bucket per provider from its policy."""
        for policy in self._policies.all():
            if policy.rpm is not None:
                self._buckets[policy.provider] = TokenBucket(
                    float(policy.rpm), float(policy.rpm) / 60.0, clock=self._clock
                )
            if policy.max_concurrency is not None:
                self._limiters[policy.provider] = LocalLimiter(policy.max_concurrency)

    def _emit_interrupt(self, envelope: Any) -> None:
        """Route an INTERRUPT_REQUEST to the reservation's callback endpoint.

        The callback_endpoint is embedded in the payload reason as
        ``BUDGET:<socket-path>``. The daemon writes the envelope to that
        socket (best-effort; the engine may be gone).
        """
        reason = str(envelope.payload.reason)
        if not reason.startswith("BUDGET:"):
            return
        endpoint = reason[len("BUDGET:") :]
        try:
            from dev_harness.ipc.client import IpcClient

            client = IpcClient(endpoint)
            client.send(envelope)
            client.close()
        except Exception:  # noqa: BLE001, S110 - best-effort callback; must never raise
            # Best-effort: the engine may be down or the platform lacks AF_UNIX;
            # the kill-switch still trips regardless.
            pass

    def acquire_lock(self) -> None:
        """Acquire the host-scoped single-instance lock (exclusive)."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_handle = self.lock_path.open("a+", encoding="utf-8")
            import portalocker

            portalocker.lock(self._lock_handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except (OSError, ValueError, portalocker.exceptions.AlreadyLocked) as exc:
            raise AlreadyRunningError(
                "another broker instance holds the host lock",
                remediation="Stop the existing broker or remove a stale lock file.",
            ) from exc

    def release_lock(self) -> None:
        """Release the single-instance lock."""
        if self._lock_handle is not None:
            try:
                import portalocker

                portalocker.unlock(self._lock_handle)
            except (OSError, ValueError):
                pass
            self._lock_handle.close()
            self._lock_handle = None

    def start(self) -> None:
        """Acquire the lock and start the socket server."""
        self.acquire_lock()
        self._running = True
        self._server = self._make_server()
        self._server.start()

    def _make_server(self) -> Any:
        """Build the broker socket server."""
        return _BrokerSocketServer(self, socket_factory=self._socket_factory)

    def _handle_message(self, msg: BrokerMessage) -> BrokerMessage:
        """Dispatch a broker message to an operation."""
        op = msg.op
        if op == "HEALTH":
            return BrokerMessage(op="HEALTH", ok=True, data={"status": "ok"})
        if op == "RESERVE":
            return self._reserve(msg)
        if op == "COMMIT":
            return self._commit(msg)
        if op == "RELEASE":
            return self._release(msg)
        if op == "METRICS":
            return self._metrics_reply()
        return BrokerMessage(op=op, ok=False, data={"reason": "unknown_op"})

    def _reserve(self, msg: BrokerMessage) -> BrokerMessage:
        """Handle a RESERVE request."""
        try:
            self._kill.check()
            provider = ProviderId(msg.data["provider"])
            tokens = float(msg.data.get("tokens", 1.0))
            callback = str(msg.data.get("callback_endpoint", ""))
            bucket = self._buckets.get(provider)
            if bucket is not None and not bucket.try_acquire(tokens):
                return BrokerMessage(op="RESERVE", ok=False, data={"reason": "rate_limited"})
            limiter = self._limiters.get(provider)
            if limiter is not None and not limiter.acquire(timeout=0):
                if bucket is not None:
                    bucket.release(tokens)
                return BrokerMessage(op="RESERVE", ok=False, data={"reason": "saturated"})
            res = self._reservations.create(provider, tokens, callback)
            return BrokerMessage(
                op="RESERVE",
                ok=True,
                data={
                    "granted": True,
                    "reservation_id": res.reservation_id,
                    "provider": provider.value,
                    "tokens": tokens,
                },
            )
        except BudgetExceededError:
            return BrokerMessage(op="RESERVE", ok=False, data={"reason": "budget"})
        except (KeyError, ValueError, TypeError):
            return BrokerMessage(op="RESERVE", ok=False, data={"reason": "error"})

    def _commit(self, msg: BrokerMessage) -> BrokerMessage:
        """Handle a COMMIT request."""
        rid = str(msg.data.get("reservation_id", ""))
        actual = float(msg.data.get("actual", 0.0))
        model = str(msg.data.get("model", ""))
        usage_in = int(msg.data.get("usage_in", 0))
        usage_out = int(msg.data.get("usage_out", 0))
        provider = ProviderId(msg.data["provider"])
        delta = self._reservations.commit(rid, actual)
        bucket = self._buckets.get(provider)
        if bucket is not None and delta > 0:
            bucket.release(delta)
        if model:
            from dev_harness.contracts.llm import Usage

            try:
                self._governor.commit(model, Usage(usage_in, usage_out))
                with self._tpm_lock:
                    self._tpm_burn += usage_in + usage_out
                self._record_latency(0.0)
                self._metrics["cumulative_usd"] = self._governor.run_usd
            except BudgetExceededError:
                self._kill.trip(str(msg.data.get("callback_endpoint", "")))
        return BrokerMessage(op="COMMIT", ok=True, data={"delta": delta})

    def _release(self, msg: BrokerMessage) -> BrokerMessage:
        """Handle a RELEASE request."""
        rid = str(msg.data.get("reservation_id", ""))
        provider = ProviderId(msg.data["provider"])
        tokens = self._reservations.release(rid)
        bucket = self._buckets.get(provider)
        if bucket is not None and tokens > 0:
            bucket.release(tokens)
        limiter = self._limiters.get(provider)
        if limiter is not None:
            limiter.release()
        return BrokerMessage(op="RELEASE", ok=True, data={"tokens": tokens})

    def _metrics_reply(self) -> BrokerMessage:
        """Return the current metrics snapshot."""
        return BrokerMessage(
            op="METRICS",
            ok=True,
            data={
                "p50_latency_ms": self._metrics["p50_latency_ms"],
                "p95_latency_ms": self._metrics["p95_latency_ms"],
                "tpm_burn": self._tpm_burn,
                "cumulative_usd": self._metrics["cumulative_usd"],
            },
        )

    def _record_latency(self, latency_ms: float) -> None:
        """Record a latency sample and recompute p50/p95."""
        with self._lat_lock:
            self._latencies.append(latency_ms)
            samples = sorted(self._latencies)
            n = len(samples)
            if n:
                self._metrics["p50_latency_ms"] = samples[min(n - 1, int(n * 0.5))]
                self._metrics["p95_latency_ms"] = samples[min(n - 1, int(n * 0.95))]

    def drain(self, timeout: float = 5.0) -> None:
        """Graceful drain: stop accepting, wait for in-flight reservations."""
        self._draining = True
        self._running = False
        if self._server is not None:
            self._server.stop()
        deadline = self._clock() + timeout
        while self._reservations.live_count() > 0 and self._clock() < deadline:
            self._reservations.reap_expired()
            time.sleep(0.01)
        self._reservations.reap_expired()
        self.release_lock()

    def stop(self) -> None:
        """Stop the daemon."""
        self._running = False
        if self._server is not None:
            self._server.stop()
        self.release_lock()


class _BrokerSocketServer:
    """A minimal threaded AF_UNIX server speaking the broker protocol."""

    def __init__(
        self,
        daemon: BrokerDaemon,
        *,
        socket_factory: Callable[[int, int], Any] | None = None,
    ) -> None:
        self._daemon = daemon
        self._socket_factory = socket_factory or socket.socket
        self._sock: Any = None
        self._running = False
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        path = self._daemon.socket_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                path.unlink()
            except (OSError, NotImplementedError):
                os.unlink(path)
        self._sock = self._socket_factory(_AF_UNIX, _SOCK_STREAM)
        self._sock.bind(str(path))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self._sock.listen(16)
        self._running = True
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            t = threading.Thread(target=self._handle_conn, args=(conn,), daemon=True)
            self._threads.append(t)
            t.start()

    def _handle_conn(self, conn: Any) -> None:
        try:
            with conn:
                while self._running:
                    try:
                        msg = read_frame(conn)
                    except (OSError, ValueError):
                        break
                    reply = self._daemon._handle_message(msg)
                    conn.sendall(encode(reply))
        except OSError:
            pass

    def stop(self) -> None:
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    """Broker daemon entry point."""
    import argparse

    parser = argparse.ArgumentParser(prog="dev-harness-broker")
    parser.add_argument("--config", default=None, help="Path to a TOML config")
    parser.add_argument("--socket", default=None, help="Override the host-scoped socket path")
    parser.add_argument("--lock", default=None, help="Override the host-scoped lock path")
    parser.add_argument("--health", action="store_true", help="Check health and exit")
    args = parser.parse_args(argv)

    if args.health:
        return _health(args.socket)

    config = HarnessConfig()
    if args.config:
        from dev_harness.config import load_config

        config = load_config(args.config)
    daemon = BrokerDaemon(config, socket_path=args.socket, lock_path=args.lock)
    try:
        daemon.start()
    except AlreadyRunningError:
        print("AlreadyRunning", file=sys.stderr)
        return 3
    print(f"broker listening on {daemon.socket_path}", file=sys.stderr)
    try:
        while daemon._running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        daemon.drain()
    return 0


def _health(socket_path: str | None) -> int:
    """Check broker health via the IPC endpoint."""
    path = Path(socket_path) if socket_path else DEFAULT_SOCKET_PATH
    try:
        from dev_harness.broker.client import BrokerClient

        client = BrokerClient(path)
        client.health()
        client.close()
        print(json.dumps({"status": "ok"}))
        return 0
    except (OSError, ValueError):
        print(json.dumps({"status": "unavailable"}))
        return 1
