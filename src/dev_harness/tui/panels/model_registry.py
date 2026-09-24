"""Model-registry panel — provider/model + rate-limit metrics (V11 task 7.5).

``#model-registry`` renders a ``DataTable`` of the active provider/model and the
latest rate-limit metrics: p50/p95 latency, TPM burn and cumulative USD. It is
pure rendering: no engine logic, no ``tui/ -> engine/`` import — every value
comes from an event payload. Envelopes arrive via a
:class:`~dev_harness.tui.bridge.Bridge` whose handlers run on the Textual UI
thread, so the panel writes directly.

Precision contract (7.B): with no feed every metric cell shows an em dash
(``—``); once fed, p50/p95 render to 1 decimal place and USD to 4 decimal
places. ``MODEL_CONFIG_CHANGE`` updates the provider and active-model cells.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable
from textual.widgets.data_table import ColumnKey

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import (
    Envelope,
    MetricsUpdatePayload,
    ModelConfigChangePayload,
)

if TYPE_CHECKING:
    from dev_harness.tui.bridge import Bridge

#: DataTable widget id for the registry rows.
REGISTRY_TABLE_ID = "#registry-table"

#: The em dash shown for a metric with no feed yet.
EMPTY = "—"

#: Fixed row keys for the registry cells.
_PROVIDER_ROW = "provider"
_MODEL_ROW = "model"
_P50_ROW = "p50"
_P95_ROW = "p95"
_TPM_ROW = "tpm"
_USD_ROW = "usd"
#: Column keys for the two-column registry table.
_FIELD_COL = "field"
_VALUE_COL = "value"


def format_latency(ms: float | None) -> str:
    """Render a latency in milliseconds to 1 decimal place (``—`` when ``None``)."""
    if ms is None:
        return EMPTY
    return f"{ms:.1f}ms"


def format_usd(usd: float | None) -> str:
    """Render a USD amount to 4 decimal places (``—`` when ``None``)."""
    if usd is None:
        return EMPTY
    return f"${usd:.4f}"


class ModelRegistry(Vertical):
    """Provider/model + rate-limit metrics ``DataTable`` for ``#model-registry``."""

    DEFAULT_CSS = """
    ModelRegistry {
        layout: vertical;
    }
    ModelRegistry > #registry-table {
        height: 1fr;
        width: 1fr;
    }
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._table: DataTable[str] = DataTable(id="registry-table")
        #: Column key for the value column; created on mount (needs an active app).
        self._value_col: ColumnKey | None = None
        #: Active provider; empty until the first MODEL_CONFIG_CHANGE.
        self._provider = ""
        #: Active model; empty until the first MODEL_CONFIG_CHANGE.
        self._active_model = ""
        #: Rendered metric cells; em dash until the first METRICS_UPDATE.
        self._p50_text = EMPTY
        self._p95_text = EMPTY
        self._tpm_text = EMPTY
        self._usd_text = EMPTY

    def compose(self) -> ComposeResult:
        """Yield the registry table as the sole vertical child."""
        yield self._table

    def on_mount(self) -> None:
        """Create the fixed table columns and rows."""
        self._ensure_rows()

    def on_metrics(self, payload: MetricsUpdatePayload) -> None:
        """Update the p50/p95/TPM/USD cells from a ``METRICS_UPDATE``."""
        self._p50_text = format_latency(payload.p50_latency_ms)
        self._p95_text = format_latency(payload.p95_latency_ms)
        self._tpm_text = str(payload.tpm_burn)
        self._usd_text = format_usd(payload.cumulative_usd)
        self._refresh_metrics()

    def on_model_config(self, payload: ModelConfigChangePayload) -> None:
        """Update the provider and active-model cells from a ``MODEL_CONFIG_CHANGE``."""
        self._provider = payload.provider
        self._active_model = payload.model
        self._refresh_model()

    def bind(self, bridge: Bridge) -> None:
        """Register this panel's handlers with a bridge (callbacks run on the UI thread)."""
        bridge.on(EventType.METRICS_UPDATE, self._handle_metrics)
        bridge.on(EventType.MODEL_CONFIG_CHANGE, self._handle_model_config)

    def _handle_metrics(self, env: Envelope) -> None:
        payload = env.payload
        if isinstance(payload, MetricsUpdatePayload):
            self.on_metrics(payload)

    def _handle_model_config(self, env: Envelope) -> None:
        payload = env.payload
        if isinstance(payload, ModelConfigChangePayload):
            self.on_model_config(payload)

    def _ensure_rows(self) -> None:
        """Create the columns and fixed rows once (idempotent)."""
        if self._value_col is None:
            self._value_col = self._table.add_columns(_FIELD_COL, _VALUE_COL)[1]
        for key, value in (
            (_PROVIDER_ROW, self._provider),
            (_MODEL_ROW, self._active_model),
            (_P50_ROW, self._p50_text),
            (_P95_ROW, self._p95_text),
            (_TPM_ROW, self._tpm_text),
            (_USD_ROW, self._usd_text),
        ):
            if key not in self._table.rows:
                self._table.add_row(key, value, key=key)

    def _refresh_metrics(self) -> None:
        """Write the metric cells in place (no-op when unmounted)."""
        if not self.is_mounted:
            return
        self._ensure_rows()
        if self._value_col is None:
            return
        self._table.update_cell(_P50_ROW, self._value_col, self._p50_text)
        self._table.update_cell(_P95_ROW, self._value_col, self._p95_text)
        self._table.update_cell(_TPM_ROW, self._value_col, self._tpm_text)
        self._table.update_cell(_USD_ROW, self._value_col, self._usd_text)

    def _refresh_model(self) -> None:
        """Write the provider/model cells in place (no-op when unmounted)."""
        if not self.is_mounted:
            return
        self._ensure_rows()
        if self._value_col is None:
            return
        self._table.update_cell(_PROVIDER_ROW, self._value_col, self._provider)
        self._table.update_cell(_MODEL_ROW, self._value_col, self._active_model)

    @property
    def provider(self) -> str:
        """The provider from the last ``MODEL_CONFIG_CHANGE`` (empty before any)."""
        return self._provider

    @property
    def active_model(self) -> str:
        """The model from the last ``MODEL_CONFIG_CHANGE`` (empty before any)."""
        return self._active_model

    @property
    def latency_text(self) -> str:
        """The rendered p50 latency cell (``—`` before any feed)."""
        return self._p50_text

    @property
    def p95_text(self) -> str:
        """The rendered p95 latency cell (``—`` before any feed)."""
        return self._p95_text

    @property
    def usd_text(self) -> str:
        """The rendered cumulative-USD cell (``—`` before any feed)."""
        return self._usd_text

    @property
    def tpm_text(self) -> str:
        """The rendered TPM-burn cell (``—`` before any feed)."""
        return self._tpm_text
