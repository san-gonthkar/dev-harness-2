"""Engine<->broker wiring: all provider calls route through the broker client (V11 5.5).

The gateway is the only engine-side entry point for provider capacity. Every
call goes through BrokerClient (fail-closed); no provider adapter is ever
imported or called from engine code (enforced by an AST guard test). When the
active model changes, the gateway emits a MODEL_CONFIG_CHANGE envelope.
"""

from __future__ import annotations

from collections.abc import Callable

from dev_harness.broker.client import BrokerClient
from dev_harness.contracts.enums import EventType, ProviderId
from dev_harness.contracts.errors import BrokerUnavailableError
from dev_harness.contracts.events import Envelope, ModelConfigChangePayload

# Emitted when the active model changes (producer: 5.5).
OnModelChange = Callable[[Envelope], None]


class ProviderGateway:
    """Routes provider capacity calls through the broker client."""

    def __init__(
        self,
        broker: BrokerClient,
        *,
        on_model_change: OnModelChange | None = None,
    ) -> None:
        self._broker = broker
        self._on_model_change = on_model_change
        self._active_model: str = ""

    @property
    def active_model(self) -> str:
        """The currently active model name (empty until set)."""
        return self._active_model

    def set_active_model(self, model: str) -> bool:
        """Set the active model; emits MODEL_CONFIG_CHANGE when it changes.

        Returns True when the model actually changed.
        """
        if model == self._active_model:
            return False
        self._active_model = model
        if self._on_model_change is not None:
            self._on_model_change(
                Envelope(
                    type=EventType.MODEL_CONFIG_CHANGE,
                    payload=ModelConfigChangePayload(
                        type="MODEL_CONFIG_CHANGE",
                        provider="",
                        model=model,
                    ),
                )
            )
        return True

    def reserve(
        self,
        provider: ProviderId | str,
        *,
        tokens: float = 1.0,
        callback_endpoint: str = "",
    ) -> str:
        """Reserve capacity through the broker; returns the reservation id."""
        reply = self._broker.reserve(
            provider, tokens=tokens, callback_endpoint=callback_endpoint
        )
        return str(reply.data.get("reservation_id", ""))

    def commit(
        self,
        provider: ProviderId | str,
        reservation_id: str,
        *,
        actual: float = 0.0,
        model: str = "",
        usage_in: int = 0,
        usage_out: int = 0,
        callback_endpoint: str = "",
    ) -> None:
        """Commit a reservation with actual usage through the broker."""
        self._broker.commit(
            provider,
            reservation_id,
            actual=actual,
            model=model,
            usage_in=usage_in,
            usage_out=usage_out,
            callback_endpoint=callback_endpoint,
        )

    def release(self, provider: ProviderId | str, reservation_id: str) -> None:
        """Release a reservation through the broker."""
        self._broker.release(provider, reservation_id)

    def metrics(self) -> dict[str, object]:
        """Fetch the broker's metrics snapshot."""
        reply = self._broker.metrics()
        return dict(reply.data)

    def health(self) -> bool:
        """True when the broker is reachable."""
        try:
            reply = self._broker.health()
        except BrokerUnavailableError:
            return False
        return bool(reply.ok)

    def close(self) -> None:
        """Close the underlying broker connection."""
        self._broker.close()
