"""Ollama adapter tests (V11 3.6)."""

from __future__ import annotations

import json
import time

import pytest
import pytest_asyncio

from dev_harness.contracts.llm import Message
from dev_harness.providers._fake import FakeProviderServer
from dev_harness.providers.ollama import COLD_START_TTFB, OllamaClient

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def fake() -> FakeProviderServer:
    key = json.dumps([{"role": "user", "content": "hello"}])
    server = FakeProviderServer(scripted={key: "world"})
    yield server
    await server.client().aclose()


@pytest.mark.asyncio
async def test_complete_round_trip(fake: FakeProviderServer) -> None:
    client = OllamaClient(base_url="http://fake", client=fake.client())
    text, _ = await client.complete([Message(role="user", content="hello")])
    assert text == "world"


@pytest.mark.asyncio
async def test_num_ctx_keep_alive_in_body(fake: FakeProviderServer) -> None:
    client = OllamaClient(
        base_url="http://fake", client=fake.client(), num_ctx=16384, keep_alive="10m"
    )
    await client.complete([Message(role="user", content="hello")])
    assert fake.requests
    body = fake.requests[-1]["body"]
    assert body["options"]["num_ctx"] == 16384
    assert body["options"]["keep_alive"] == "10m"


@pytest.mark.asyncio
async def test_stream_reassembles(fake: FakeProviderServer) -> None:
    client = OllamaClient(base_url="http://fake", client=fake.client())
    chunks = [c async for c in client.stream([Message(role="user", content="hello")])]
    reassembled = "".join(c.token for c in chunks)
    assert reassembled == "world"
    seqs = [c.seq for c in chunks]
    assert seqs == list(range(len(chunks)))


@pytest.mark.asyncio
async def test_cold_start_marks_model_loading(
    fake: FakeProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first-token latency above COLD_START_TTFB marks model_loading=True."""
    client = OllamaClient(base_url="http://fake", client=fake.client())
    # Simulate a slow first token by patching time.monotonic.
    real = time.monotonic
    calls = {"n": 0}

    def slow_monotonic() -> float:
        calls["n"] += 1
        if calls["n"] == 1:
            return real()  # start
        return real() + COLD_START_TTFB + 1  # first token arrives late

    monkeypatch.setattr(time, "monotonic", slow_monotonic)
    chunks = [c async for c in client.stream([Message(role="user", content="hello")])]
    assert chunks
    assert chunks[0].model_loading is True
    # Subsequent chunks are not loading.
    assert all(not c.model_loading for c in chunks[1:])


@pytest.mark.asyncio
async def test_fast_start_not_loading(fake: FakeProviderServer) -> None:
    client = OllamaClient(base_url="http://fake", client=fake.client())
    chunks = [c async for c in client.stream([Message(role="user", content="hello")])]
    assert chunks
    assert all(not c.model_loading for c in chunks)


def test_count_tokens_estimate() -> None:
    client = OllamaClient()
    assert client.count_tokens("abcd") == 1
