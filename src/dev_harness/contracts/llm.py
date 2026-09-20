"""Neutral LLM types shared across providers (V11 3.1).

These are provider-agnostic: Message, ToolCall, Usage, TokenChunk. Adapters
map provider-specific shapes onto these.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    """A single chat message."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: str | None = None


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation requested by the model."""

    name: str
    arguments: str  # JSON-encoded arguments


@dataclass(frozen=True)
class Usage:
    """Token usage accounting."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class TokenChunk:
    """A streamed token chunk with a sequence number."""

    token: str
    seq: int
    model_loading: bool = False
