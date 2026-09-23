"""Interrupt-engine metrics collector tests (V11 6.7).

The collector feeds the p50/p95 fields of ``METRICS_UPDATE`` (4.10). These are
unit tests: no real interrupts, no network.
"""

from __future__ import annotations

import pytest

from dev_harness.contracts.events import MetricsUpdatePayload
from dev_harness.core.metrics import COUNTER_NAMES, InterruptMetrics

pytestmark = pytest.mark.unit


def test_counters_start_at_zero() -> None:
    """A fresh collector has every counter at zero and no samples."""
    metrics = InterruptMetrics()

    assert metrics.counters == dict.fromkeys(COUNTER_NAMES, 0)
    assert metrics.samples == 0


def test_record_methods_increment_counters() -> None:
    """Each record_* method increments its counter."""
    metrics = InterruptMetrics()

    metrics.record_interrupt(12.0)
    metrics.record_interrupt(20.0)
    metrics.record_escalation()
    metrics.record_reap(3)
    metrics.record_seal()

    assert metrics.counters == {
        "interrupts_issued": 2,
        "escalations": 1,
        "reaps": 3,
        "seals": 1,
    }
    assert metrics.samples == 2


def test_counters_keys_match_canonical_names() -> None:
    """The counters snapshot exposes exactly the canonical names."""
    assert tuple(InterruptMetrics().counters) == COUNTER_NAMES


@pytest.mark.parametrize(
    ("p", "expected"),
    [
        (0.0, 10.0),
        (50.0, 50.0),
        (95.0, 100.0),
        (100.0, 100.0),
    ],
)
def test_percentile_nearest_rank(p: float, expected: float) -> None:
    """Nearest-rank percentiles over a known 10-sample histogram."""
    metrics = InterruptMetrics()
    for value in (10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0):
        metrics.record_interrupt(value)

    assert metrics.percentile(p) == expected


def test_empty_histogram_is_zero() -> None:
    """An empty histogram reports zero for every latency statistic."""
    metrics = InterruptMetrics()

    assert metrics.percentile(95.0) == 0.0
    assert metrics.p50() == 0.0
    assert metrics.p95() == 0.0
    assert metrics.max_latency_ms() == 0.0


def test_p50_p95_and_max() -> None:
    """p50/p95/max reflect the recorded samples."""
    metrics = InterruptMetrics()
    for value in (100.0, 200.0, 300.0, 400.0):
        metrics.record_interrupt(value)

    assert metrics.p50() == 200.0
    assert metrics.p95() == 400.0
    assert metrics.max_latency_ms() == 400.0


def test_to_payload_maps_histogram_to_metrics_update() -> None:
    """to_payload() builds a METRICS_UPDATE payload from the histogram."""
    metrics = InterruptMetrics()
    metrics.record_interrupt(10.0)
    metrics.record_interrupt(30.0)

    payload = metrics.to_payload(tpm_burn=1234, cumulative_usd=0.5)

    assert payload == MetricsUpdatePayload(
        type="METRICS_UPDATE",
        p50_latency_ms=10.0,
        p95_latency_ms=30.0,
        tpm_burn=1234,
        cumulative_usd=0.5,
    )


def test_reset_clears_counters_and_histogram() -> None:
    """reset() zeroes every counter and empties the histogram."""
    metrics = InterruptMetrics()
    metrics.record_interrupt(5.0)
    metrics.record_escalation()
    metrics.record_reap()
    metrics.record_seal()

    metrics.reset()

    assert metrics.counters == dict.fromkeys(COUNTER_NAMES, 0)
    assert metrics.samples == 0
    assert metrics.p95() == 0.0
