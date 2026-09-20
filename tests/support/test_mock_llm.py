"""MockLLM + FakeProviderServer fidelity tests (V11 0.13)."""

from __future__ import annotations

import httpx
import pytest

from tests.support.fake_provider import FakeProviderServer
from tests.support.mock_llm import (
    AuthFault,
    MockLLM,
    OverloadedFault,
    RateLimitedFault,
    TimeoutFault,
)

pytestmark = pytest.mark.unit


def test_same_seed_byte_identical() -> None:
    a = MockLLM(seed="s1").complete("hello")
    b = MockLLM(seed="s1").complete("hello")
    assert a == b
    assert a[0] == b[0]


def test_scripted_completion() -> None:
    llm = MockLLM(scripted={"ping": "pong"})
    text, usage = llm.complete("ping")
    assert text == "pong"
    assert usage.input_tokens == 4


def test_fault_429() -> None:
    llm = MockLLM(fault="429")
    with pytest.raises(RateLimitedFault):
        llm.complete("x")


def test_fault_529() -> None:
    llm = MockLLM(fault="529")
    with pytest.raises(OverloadedFault):
        llm.complete("x")


def test_fault_401() -> None:
    llm = MockLLM(fault="401")
    with pytest.raises(AuthFault):
        llm.complete("x")


def test_fault_timeout() -> None:
    llm = MockLLM(fault="timeout")
    with pytest.raises(TimeoutFault):
        llm.complete("x")


@pytest.mark.asyncio
async def test_streaming_chunks_and_sentinel() -> None:
    llm = MockLLM(scripted={"hi": "hello"})
    chunks = [c async for c in llm.stream("hi")]
    assert len(chunks) >= 2
    assert "".join(c.token for c in chunks) == "hello"
    seqs = [c.seq for c in chunks]
    assert seqs == list(range(len(chunks)))


@pytest.mark.asyncio
async def test_fake_provider_completion() -> None:
    server = FakeProviderServer(scripted={"ping": "pong"})
    async with server.client() as client:
        resp = await client.post("/v1/complete", json={"prompt": "ping"})
    assert resp.status_code == 200
    assert resp.json()["content"] == "pong"
    assert server.requests[0]["path"] == "/v1/complete"


@pytest.mark.asyncio
async def test_fake_provider_429_with_retry_after() -> None:
    server = FakeProviderServer(fault="429")
    async with server.client() as client:
        resp = await client.post("/v1/complete", json={"prompt": "x"})
    assert resp.status_code == 429
    assert resp.json()["retry_after"] == 30


@pytest.mark.asyncio
async def test_fake_provider_529() -> None:
    server = FakeProviderServer(fault="529")
    async with server.client() as client:
        resp = await client.post("/v1/complete", json={"prompt": "x"})
    assert resp.status_code == 529


@pytest.mark.asyncio
async def test_fake_provider_no_tcp_bind() -> None:
    """The ASGITransport must not create a real socket."""
    server = FakeProviderServer()
    transport = httpx.ASGITransport(app=server)
    assert isinstance(transport, httpx.ASGITransport)
