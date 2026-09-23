"""Core interrupt-engine metrics collector (V11 6.7).

A small typed collector for the interrupt engine: counters for interrupts
issued, escalations, reaps and seals, plus a latency histogram that feeds the
p50/p95 fields of ``METRICS_UPDATE`` (4.10). No third-party metrics library.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from dev_harness.contracts.events import MetricsUpdatePayload

# Canonical counter names, in display order.
COUNTER_NAMES: tuple[str, ...] = (
    "interrupts_issued",
    "escalations",
    "reaps",
    "seals",
)


@dataclass(slots=True)
class InterruptMetrics:
    """Counters + latency histogram for the interrupt engine."""

    interrupts_issued: int = 0
    escalations: int = 0
    reaps: int = 0
    seals: int = 0
    _latencies_ms: list[float] = field(default_factory=list, repr=False)

    def record_interrupt(self, latency_ms: float) -> None:
        """Count one interrupt and add its latency to the histogram."""
        self.interrupts_issued += 1
        self._latencies_ms.append(latency_ms)

    def record_escalation(self) -> None:
        """Count one SIGINT -> SIGKILL escalation."""
        self.escalations += 1

    def record_reap(self, count: int = 1) -> None:
        """Count ``count`` reaped processes."""
        self.reaps += count

    def record_seal(self) -> None:
        """Count one pause seal."""
        self.seals += 1

    @property
    def counters(self) -> dict[str, int]:
        """A snapshot of every counter by canonical name."""
        return {
            "interrupts_issued": self.interrupts_issued,
            "escalations": self.escalations,
            "reaps": self.reaps,
            "seals": self.seals,
        }

    @property
    def samples(self) -> int:
        """The number of latency samples in the histogram (a gauge)."""
        return len(self._latencies_ms)

    def percentile(self, p: float) -> float:
        """Nearest-rank percentile of the latency histogram (0.0 if empty)."""
        if not self._latencies_ms:
            return 0.0
        ordered = sorted(self._latencies_ms)
        rank = math.ceil(p / 100.0 * len(ordered))
        index = min(max(rank - 1, 0), len(ordered) - 1)
        return ordered[index]

    def p50(self) -> float:
        """Median interrupt latency in milliseconds."""
        return self.percentile(50.0)

    def p95(self) -> float:
        """95th-percentile interrupt latency in milliseconds."""
        return self.percentile(95.0)

    def max_latency_ms(self) -> float:
        """Maximum observed interrupt latency in milliseconds."""
        return max(self._latencies_ms) if self._latencies_ms else 0.0

    def to_payload(
        self, *, tpm_burn: int = 0, cumulative_usd: float = 0.0
    ) -> MetricsUpdatePayload:
        """Build a ``METRICS_UPDATE`` payload from the histogram."""
        return MetricsUpdatePayload(
            type="METRICS_UPDATE",
            p50_latency_ms=self.p50(),
            p95_latency_ms=self.p95(),
            tpm_burn=tpm_burn,
            cumulative_usd=cumulative_usd,
        )

    def reset(self) -> None:
        """Zero every counter and clear the histogram."""
        self.interrupts_issued = 0
        self.escalations = 0
        self.reaps = 0
        self.seals = 0
        self._latencies_ms.clear()
