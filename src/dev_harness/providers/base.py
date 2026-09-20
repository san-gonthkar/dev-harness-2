"""LLMClient protocol and provider base (V11 3.1).

All three adapters (Anthropic, OpenRouter, Ollama) satisfy this
runtime_checkable Protocol so the pipeline can treat them interchangeably.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from dev_harness.contracts.llm import Message, TokenChunk, Usage


@runtime_checkable
class LLMClient(Protocol):
    """The provider-agnostic client interface."""

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        """Return (text, usage) for a non-streaming completion."""
        ...

    def stream(
        self, messages: list[Message], *, model: str | None = None
    ) -> AsyncIterator[TokenChunk]:
        """Yield token chunks for a streaming completion (async generator)."""
        ...

    def count_tokens(self, text: str) -> int:
        """Return the token count for text."""
        ...
