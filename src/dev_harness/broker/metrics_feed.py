"""Metrics feed: p50/p95 latency, TPM burn, cumulative USD (V11 4.10).

The feed samples the broker's metrics and emits METRICS_UPDATE envelopes
(EventType.METRICS_UPDATE) at a fixed interval so the TUI model-registry
panel can display live numbers.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from dev_harness.broker.client import BrokerClient
from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope, MetricsUpdatePayload

Emit = Callable[[Envelope], None]


class MetricsFeed:
    """Periodically emits METRICS_UPDATE envelopes from broker metrics."""

    def __init__(
        self,
        client: BrokerClient,
        emit: Emit,
        *,
        interval: float = 1.0,
    ) -> None:
        self._client = client
        self._emit = emit
        self.interval = interval
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background sampling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        """Sample and emit until stopped."""
        while self._running:
            try:
                reply = self._client.metrics()
                data = reply.data
                self._emit(
                    Envelope(
                        type=EventType.METRICS_UPDATE,
                        payload=MetricsUpdatePayload(
                            type="METRICS_UPDATE",
                            p50_latency_ms=float(data.get("p50_latency_ms", 0.0)),
                            p95_latency_ms=float(data.get("p95_latency_ms", 0.0)),
                            tpm_burn=int(data.get("tpm_burn", 0)),
                            cumulative_usd=float(data.get("cumulative_usd", 0.0)),
                        ),
                    )
                )
            except Exception:  # noqa: BLE001, S110 - background sampler must never die
                # Fail-closed: swallow any error and keep sampling; a dead
                # sampler would silently stop the metrics feed.
                pass
            time.sleep(self.interval)

    def stop(self) -> None:
        """Stop the sampling thread."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
