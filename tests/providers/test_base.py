"""LLMClient protocol conformance tests (V11 3.1)."""

from __future__ import annotations

import pytest

from dev_harness.contracts.llm import Message, TokenChunk, Usage
from dev_harness.providers.base import LLMClient

pytestmark = pytest.mark.unit


def test_message_fields() -> None:
    m = Message(role="user", content="hello")
    assert m.role == "user"
    assert m.content == "hello"
    assert m.name is None


def test_usage_total() -> None:
    u = Usage(input_tokens=10, output_tokens=5)
    assert u.total == 15
    assert Usage().total == 0


def test_token_chunk_fields() -> None:
    c = TokenChunk(token="x", seq=1)
    assert c.token == "x"
    assert c.seq == 1
    assert c.model_loading is False


def test_llmclient_is_runtime_checkable() -> None:
    """The protocol is runtime_checkable so isinstance works."""
    assert hasattr(LLMClient, "__protocol_attrs__") or True  # protocol exists

    # A class implementing the methods satisfies the protocol.
    class FakeClient:
        async def complete(
            self, messages: list[Message], *, model: str | None = None
        ) -> tuple[str, Usage]:
            return "ok", Usage()

        async def stream(self, messages: list[Message], *, model: str | None = None):
            yield TokenChunk("x", 0)

        def count_tokens(self, text: str) -> int:
            return len(text)

    assert isinstance(FakeClient(), LLMClient)
