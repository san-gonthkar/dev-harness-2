"""Broker-routed completion client (V11 5.5 extension).

Wraps a live provider adapter so every call reserves capacity from the broker
before it runs and commits actual usage after. This is what lets the engine
use a real provider while keeping the architecture's invariant: **all provider
capacity flows through the broker**.

The wrapper satisfies the engine's ``CompletionClient`` protocol, so the
pipeline is unchanged — it still receives an injected client and never knows
whether it is talking to a mock, a fake server, or OpenRouter.
"""

from __future__ import annotations

from dev_harness.broker.client import BrokerClient
from dev_harness.contracts.enums import ProviderId
from dev_harness.contracts.llm import Message, Usage
from dev_harness.providers.base import LLMClient


class BrokerRoutedClient:
    """A completion client that meters every call through the broker.

    :param inner: the live provider adapter.
    :param broker: the fail-closed broker client.
    :param provider: the provider id used for reserve/commit.
    :param model: the model id reported to the cost governor.
    """

    def __init__(
        self,
        inner: LLMClient,
        broker: BrokerClient,
        *,
        provider: ProviderId | str,
        model: str,
    ) -> None:
        self._inner = inner
        self._broker = broker
        self._provider = provider
        self._model = model

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        """Reserve, call the provider, then commit actual usage.

        A refused reservation raises ``BrokerUnavailableError`` (fail-closed):
        the provider is never called without capacity.
        """
        from dev_harness.broker.daemon import BrokerUnavailableError

        reply = self._broker.reserve(self._provider, tokens=1.0)
        if not reply.data.get("granted", False):
            reason = reply.data.get("reason", "unknown")
            raise BrokerUnavailableError(
                f"broker refused reservation: {reason}",
                remediation="Wait for capacity, raise the budget, or check the kill-switch.",
            )
        reservation_id = str(reply.data.get("reservation_id", ""))
        try:
            text, usage = await self._inner.complete(
                messages, model=model or self._model
            )
        except BaseException:
            # Release the reservation so a failed call does not leak capacity.
            self._broker.release(self._provider, reservation_id)
            raise
        self._broker.commit(
            self._provider,
            reservation_id,
            actual=1.0,
            model=model or self._model,
            usage_in=usage.input_tokens,
            usage_out=usage.output_tokens,
        )
        return text, usage

    def count_tokens(self, text: str) -> int:
        """Delegate token counting to the adapter."""
        return self._inner.count_tokens(text)

    def close(self) -> None:
        """Close the broker connection."""
        self._broker.close()
