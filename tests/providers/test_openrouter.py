"""OpenRouter adapter tests (V11 3.5)."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from dev_harness.contracts.llm import Message
from dev_harness.providers._fake import FakeProviderServer
from dev_harness.providers.openrouter import OpenRouterClient

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def fake() -> FakeProviderServer:
    key = json.dumps([{"role": "user", "content": "hello"}])
    server = FakeProviderServer(scripted={key: "world"})
    yield server
    await server.client().aclose()


@pytest.mark.asyncio
async def test_complete_round_trip(fake: FakeProviderServer) -> None:
    client = OpenRouterClient("test-key", base_url="http://fake", client=fake.client())
    text, usage = await client.complete([Message(role="user", content="hello")])
    assert text == "world"
    assert usage.input_tokens == len(json.dumps([{"role": "user", "content": "hello"}]))
    assert usage.output_tokens == len("world")


@pytest.mark.asyncio
async def test_namespace_preserved_in_request(fake: FakeProviderServer) -> None:
    client = OpenRouterClient("test-key", base_url="http://fake", client=fake.client())
    await client.complete(
        [Message(role="user", content="hello")], model="anthropic/claude-3.5-sonnet"
    )
    # The request body must carry the namespaced model id.
    assert fake.requests
    body = fake.requests[-1]["body"]
    assert body["model"] == "anthropic/claude-3.5-sonnet"


@pytest.mark.asyncio
async def test_stream_reassembles_byte_identical(fake: FakeProviderServer) -> None:
    client = OpenRouterClient("test-key", base_url="http://fake", client=fake.client())
    chunks = [c async for c in client.stream([Message(role="user", content="hello")])]
    reassembled = "".join(c.token for c in chunks)
    assert reassembled == "world"
    seqs = [c.seq for c in chunks]
    assert seqs == list(range(len(chunks)))


@pytest.mark.asyncio
async def test_401_raises_auth(fake: FakeProviderServer) -> None:
    fake.fault = "401"
    client = OpenRouterClient("test-key", base_url="http://fake", client=fake.client())
    from dev_harness.contracts.errors import AuthError

    with pytest.raises(AuthError):
        await client.complete([Message(role="user", content="hello")])


def test_count_tokens_estimate() -> None:
    client = OpenRouterClient("test-key")
    assert client.count_tokens("abcd") == 1
