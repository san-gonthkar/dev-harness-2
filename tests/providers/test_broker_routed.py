"""Broker-routed client tests (V11 5.5 extension) — metering + fail-closed."""

from __future__ import annotations

from typing import Any

import pytest

from dev_harness.broker.daemon import BrokerUnavailableError
from dev_harness.broker.protocol import BrokerMessage
from dev_harness.contracts.enums import ProviderId
from dev_harness.contracts.llm import Message, Usage
from dev_harness.providers.broker_routed import BrokerRoutedClient


class FakeBroker:
    """Records reserve/commit/release calls and returns canned replies."""

    def __init__(self, *, granted: bool = True, reason: str = "") -> None:
        self.granted = granted
        self.reason = reason
        self.reserves: list[tuple[Any, float]] = []
        self.commits: list[dict[str, Any]] = []
        self.releases: list[str] = []
        self.closed = False

    def reserve(self, provider: Any, *, tokens: float = 1.0, **_: Any) -> BrokerMessage:
        self.reserves.append((provider, tokens))
        if not self.granted:
            return BrokerMessage(op="RESERVE", ok=False, data={"reason": self.reason})
        return BrokerMessage(
            op="RESERVE",
            ok=True,
            data={"granted": True, "reservation_id": "r1"},
        )

    def commit(
        self, provider: Any, reservation_id: str, **kwargs: Any
    ) -> BrokerMessage:
        self.commits.append({"provider": provider, "rid": reservation_id, **kwargs})
        return BrokerMessage(op="COMMIT", ok=True, data={"delta": 1.0})

    def release(self, provider: Any, reservation_id: str) -> BrokerMessage:
        self.releases.append(reservation_id)
        return BrokerMessage(op="RELEASE", ok=True, data={"tokens": 1.0})

    def close(self) -> None:
        self.closed = True


class FakeAdapter:
    """A minimal LLMClient returning canned text/usage."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises
        self.calls: list[list[Message]] = []

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        self.calls.append(messages)
        if self.raises is not None:
            raise self.raises
        return "hello", Usage(input_tokens=10, output_tokens=5)

    def count_tokens(self, text: str) -> int:
        return len(text)


def _client(broker: FakeBroker, adapter: FakeAdapter) -> BrokerRoutedClient:
    return BrokerRoutedClient(
        adapter,
        broker,
        provider=ProviderId.OPENROUTER,
        model="m1",  # type: ignore[arg-type]
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_complete_reserves_then_commits() -> None:
    broker = FakeBroker()
    adapter = FakeAdapter()
    client = _client(broker, adapter)
    text, usage = await client.complete([Message(role="user", content="hi")])
    assert text == "hello"
    assert usage.input_tokens == 10
    assert len(broker.reserves) == 1
    assert len(broker.commits) == 1
    commit = broker.commits[0]
    assert commit["usage_in"] == 10
    assert commit["usage_out"] == 5
    assert commit["model"] == "m1"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refused_reservation_never_calls_provider() -> None:
    """Fail-closed: no capacity means the provider is never invoked."""
    broker = FakeBroker(granted=False, reason="rate_limited")
    adapter = FakeAdapter()
    client = _client(broker, adapter)
    with pytest.raises(BrokerUnavailableError) as excinfo:
        await client.complete([Message(role="user", content="hi")])
    assert "rate_limited" in str(excinfo.value)
    assert adapter.calls == []
    assert broker.commits == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_failure_releases_reservation() -> None:
    """A failed call must not leak capacity."""
    broker = FakeBroker()
    adapter = FakeAdapter(raises=RuntimeError("boom"))
    client = _client(broker, adapter)
    with pytest.raises(RuntimeError):
        await client.complete([Message(role="user", content="hi")])
    assert broker.releases == ["r1"]
    assert broker.commits == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_model_override_is_forwarded() -> None:
    broker = FakeBroker()
    adapter = FakeAdapter()
    client = _client(broker, adapter)
    await client.complete([Message(role="user", content="hi")], model="other")
    assert broker.commits[0]["model"] == "other"


@pytest.mark.unit
def test_count_tokens_delegates() -> None:
    client = _client(FakeBroker(), FakeAdapter())
    assert client.count_tokens("abcd") == 4


@pytest.mark.unit
def test_close_closes_broker() -> None:
    broker = FakeBroker()
    client = _client(broker, FakeAdapter())
    client.close()
    assert broker.closed is True
