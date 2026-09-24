"""Model-registry panel tests (V11 task 7.5).

Unit tests drive the handlers and the pure formatting helpers directly; the
integration test mounts the panel under a real ``HermesApp``, feeds a
``METRICS_UPDATE`` and a ``MODEL_CONFIG_CHANGE`` through a ``Bridge`` backed by
a ``BackpressureQueue``, and asserts the rendered cells. No ``time.sleep`` — the
updates are asserted after bounded ``pilot.pause`` waits.
"""

from __future__ import annotations

import time

import pytest
from textual.widgets import DataTable

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import (
    Envelope,
    MetricsUpdatePayload,
    ModelConfigChangePayload,
)
from dev_harness.ipc.queue import BackpressureQueue
from dev_harness.tui.app import MODEL_REGISTRY_ID, HermesApp
from dev_harness.tui.bridge import Bridge
from dev_harness.tui.panels.model_registry import (
    EMPTY,
    REGISTRY_TABLE_ID,
    ModelRegistry,
    format_latency,
    format_usd,
)

SIZE = (100, 30)
#: Bounded wait for the reader thread to drain the queue.
DRAIN_DEADLINE_S = 30.0


def metrics_env(
    seq: int,
    *,
    p50: float = 1.0,
    p95: float = 2.0,
    tpm: int = 10,
    usd: float = 0.01,
) -> Envelope:
    return Envelope(
        type=EventType.METRICS_UPDATE,
        seq=seq,
        payload=MetricsUpdatePayload(
            type="METRICS_UPDATE",
            p50_latency_ms=p50,
            p95_latency_ms=p95,
            tpm_burn=tpm,
            cumulative_usd=usd,
        ),
    )


def model_env(seq: int, provider: str, model: str) -> Envelope:
    return Envelope(
        type=EventType.MODEL_CONFIG_CHANGE,
        seq=seq,
        payload=ModelConfigChangePayload(
            type="MODEL_CONFIG_CHANGE", provider=provider, model=model
        ),
    )


# --- unit: formatting helpers ----------------------------------------------


@pytest.mark.unit
def test_format_latency_none_is_em_dash() -> None:
    """No feed renders an em dash, not ``0.0ms``."""
    assert format_latency(None) == EMPTY


@pytest.mark.unit
def test_format_usd_none_is_em_dash() -> None:
    """No feed renders an em dash, not ``$0.0000``."""
    assert format_usd(None) == EMPTY


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (123.456, "123.5ms"),
        (0.0, "0.0ms"),
        (1.0, "1.0ms"),
        (99.95, "100.0ms"),
    ],
)
def test_format_latency_precision(ms: float, expected: str) -> None:
    """Latency renders to exactly 1 decimal place."""
    assert format_latency(ms) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("usd", "expected"),
    [
        (1.23456789, "$1.2346"),
        (0.0, "$0.0000"),
        (0.01, "$0.0100"),
        (12.5, "$12.5000"),
    ],
)
def test_format_usd_precision(usd: float, expected: str) -> None:
    """USD renders to exactly 4 decimal places."""
    assert format_usd(usd) == expected


# --- unit: handlers ---------------------------------------------------------


@pytest.mark.unit
def test_initial_state_shows_em_dash() -> None:
    """Before any feed every metric cell is an em dash."""
    panel = ModelRegistry()
    assert panel.latency_text == EMPTY
    assert panel.p95_text == EMPTY
    assert panel.usd_text == EMPTY
    assert panel.tpm_text == EMPTY
    assert panel.provider == ""
    assert panel.active_model == ""


@pytest.mark.unit
def test_on_metrics_updates_cells() -> None:
    """``on_metrics`` records the formatted p50/p95/TPM/USD cells."""
    panel = ModelRegistry()
    panel.on_metrics(
        MetricsUpdatePayload(
            type="METRICS_UPDATE",
            p50_latency_ms=12.34,
            p95_latency_ms=56.78,
            tpm_burn=1234,
            cumulative_usd=1.23456789,
        )
    )
    assert panel.latency_text == "12.3ms"
    assert panel.p95_text == "56.8ms"
    assert panel.tpm_text == "1234"
    assert panel.usd_text == "$1.2346"


@pytest.mark.unit
def test_on_model_config_updates_provider_and_model() -> None:
    """``on_model_config`` records the provider and active model."""
    panel = ModelRegistry()
    panel.on_model_config(
        ModelConfigChangePayload(
            type="MODEL_CONFIG_CHANGE", provider="anthropic", model="claude-x"
        )
    )
    assert panel.provider == "anthropic"
    assert panel.active_model == "claude-x"


# --- negative ---------------------------------------------------------------


@pytest.mark.negative
def test_zero_p95_renders_zero_not_em_dash() -> None:
    """``p95_latency_ms == 0.0`` is a real value, not the no-feed em dash."""
    panel = ModelRegistry()
    panel.on_metrics(
        MetricsUpdatePayload(
            type="METRICS_UPDATE",
            p50_latency_ms=0.0,
            p95_latency_ms=0.0,
            tpm_burn=0,
            cumulative_usd=0.0,
        )
    )
    assert panel.p95_text == "0.0ms"
    assert panel.p95_text != EMPTY
    assert panel.usd_text == "$0.0000"


@pytest.mark.negative
def test_empty_model_string_does_not_raise() -> None:
    """An empty model string is recorded without raising."""
    panel = ModelRegistry()
    panel.on_model_config(
        ModelConfigChangePayload(type="MODEL_CONFIG_CHANGE", provider="", model="")
    )
    assert panel.provider == ""
    assert panel.active_model == ""


# --- integration ------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bridged_metrics_and_model_render_cells() -> None:
    """Bridged ``METRICS_UPDATE`` + ``MODEL_CONFIG_CHANGE`` land in the cells."""
    app = HermesApp()
    queue = BackpressureQueue()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        panel = app.query_one(MODEL_REGISTRY_ID, ModelRegistry)
        assert app.query_one(REGISTRY_TABLE_ID, DataTable) is not None

        bridge = Bridge(app, queue=queue)
        panel.bind(bridge)
        bridge.start()

        queue.put(metrics_env(1, p50=12.34, p95=56.78, tpm=1234, usd=1.23456789))
        queue.put(model_env(2, "anthropic", "claude-x"))

        deadline = time.monotonic() + DRAIN_DEADLINE_S
        while bridge.applied < 2 and time.monotonic() < deadline:
            await pilot.pause()

        bridge.stop(timeout=2.0)

        assert bridge.applied == 2
        assert panel.p95_text == "56.8ms"
        assert panel.usd_text == "$1.2346"
        assert panel.active_model == "claude-x"
        assert panel.provider == "anthropic"