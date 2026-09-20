"""Shared adapter conformance suite (V11 3.10).

All three adapters must satisfy the LLMClient protocol and behave
identically against the FakeProviderServer (substitutability proof).
"""

from __future__ import annotations

import json

import pytest

from dev_harness.contracts.llm import Message
from dev_harness.providers._fake import FakeProviderServer
from dev_harness.providers.anthropic import AnthropicClient
from dev_harness.providers.base import LLMClient
from dev_harness.providers.ollama import OllamaClient
from dev_harness.providers.openrouter import OpenRouterClient

pytestmark = pytest.mark.contract

ADAPTERS = [
    (
        "anthropic",
        lambda c: AnthropicClient("test-key", base_url="http://fake", client=c),
    ),
    (
        "openrouter",
        lambda c: OpenRouterClient("test-key", base_url="http://fake", client=c),
    ),
    ("ollama", lambda c: OllamaClient(base_url="http://fake", client=c)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,factory", ADAPTERS, ids=[a[0] for a in ADAPTERS])
async def test_adapter_satisfies_protocol(name: str, factory) -> None:
    """Every adapter satisfies the runtime_checkable LLMClient protocol."""
    server = FakeProviderServer()
    client = factory(server.client())
    assert isinstance(client, LLMClient)
    await server.client().aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,factory", ADAPTERS, ids=[a[0] for a in ADAPTERS])
async def test_adapter_completes_against_fake(name: str, factory) -> None:
    """Every adapter completes against the fake server with populated Usage."""
    key = json.dumps([{"role": "user", "content": "ping"}])
    server = FakeProviderServer(scripted={key: "pong"})
    client = factory(server.client())
    text, usage = await client.complete([Message(role="user", content="ping")])
    assert text == "pong"
    assert usage.total > 0
    await server.client().aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,factory", ADAPTERS, ids=[a[0] for a in ADAPTERS])
async def test_adapter_streams_byte_identical(name: str, factory) -> None:
    """Every adapter streams and reassembles byte-identically."""
    key = json.dumps([{"role": "user", "content": "ping"}])
    server = FakeProviderServer(scripted={key: "pong"})
    client = factory(server.client())
    chunks = [c async for c in client.stream([Message(role="user", content="ping")])]
    reassembled = "".join(c.token for c in chunks)
    assert reassembled == "pong"
    seqs = [c.seq for c in chunks]
    assert seqs == list(range(len(chunks)))
    await server.client().aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,factory", ADAPTERS, ids=[a[0] for a in ADAPTERS])
async def test_adapter_count_tokens(name: str, factory) -> None:
    """Every adapter counts tokens."""
    server = FakeProviderServer()
    client = factory(server.client())
    assert client.count_tokens("abcd") == 1
    await server.client().aclose()
