"""Anthropic adapter tests (V11 3.4)."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from dev_harness.contracts.llm import Message
from dev_harness.providers._fake import FakeProviderServer
from dev_harness.providers.anthropic import AnthropicClient

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def fake() -> FakeProviderServer:
    # The fake server extracts the prompt as the JSON-encoded messages list.
    key = json.dumps([{"role": "user", "content": "hello"}])
    server = FakeProviderServer(scripted={key: "world"})
    yield server
    await server.client().aclose()


@pytest.mark.asyncio
async def test_complete_round_trip(fake: FakeProviderServer) -> None:
    client = AnthropicClient("test-key", base_url="http://fake", client=fake.client())
    text, usage = await client.complete([Message(role="user", content="hello")])
    assert text == "world"
    # The fake server reports input_tokens = len(JSON-encoded messages).
    assert usage.input_tokens == len(json.dumps([{"role": "user", "content": "hello"}]))
    assert usage.output_tokens == len("world")


@pytest.mark.asyncio
async def test_stream_reassembles_byte_identical(fake: FakeProviderServer) -> None:
    client = AnthropicClient("test-key", base_url="http://fake", client=fake.client())
    chunks = [c async for c in client.stream([Message(role="user", content="hello")])]
    reassembled = "".join(c.token for c in chunks)
    assert reassembled == "world"
    # seq strictly increasing with 0 gaps
    seqs = [c.seq for c in chunks]
    assert seqs == list(range(len(chunks)))


@pytest.mark.asyncio
async def test_usage_matches_fake_accounting(fake: FakeProviderServer) -> None:
    client = AnthropicClient("test-key", base_url="http://fake", client=fake.client())
    _, usage = await client.complete([Message(role="user", content="hello")])
    # The fake server reports input_tokens = len(JSON-encoded messages).
    assert usage.input_tokens == len(json.dumps([{"role": "user", "content": "hello"}]))


@pytest.mark.asyncio
async def test_429_raises_rate_limited(fake: FakeProviderServer) -> None:
    fake.fault = "429"
    client = AnthropicClient("test-key", base_url="http://fake", client=fake.client())
    from dev_harness.contracts.errors import RateLimitedError

    with pytest.raises(RateLimitedError):
        await client.complete([Message(role="user", content="hello")])


@pytest.mark.asyncio
async def test_count_tokens_estimate() -> None:
    client = AnthropicClient("test-key")
    assert client.count_tokens("abcd") == 1
    assert client.count_tokens("abcdefgh") == 2
