"""MockLLM: deterministic scripted LLM for tests (V11 0.13).

Same seed -> byte-identical completion. Supports streaming and fault injection.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class TokenChunk:
    """A streamed token chunk."""

    token: str
    seq: int


@dataclass
class Usage:
    """Token usage accounting."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class MockLLM:
    """A deterministic fake LLM client.

    ``seed`` drives a deterministic token stream; the same seed yields
    byte-identical completions across runs.
    """

    seed: str = "mock"
    scripted: dict[str, str] = field(default_factory=dict)
    fault: str | None = None  # "429" | "500" | "529" | "401" | "timeout"

    def _derive(self, prompt: str) -> str:
        if prompt in self.scripted:
            return self.scripted[prompt]
        digest = hashlib.sha256((self.seed + prompt).encode("utf-8")).hexdigest()
        return f"completion-{digest[:16]}"

    def complete(self, prompt: str) -> tuple[str, Usage]:
        """Return (text, usage) deterministically."""
        if self.fault == "429":
            raise RateLimitedFault("rate limited")
        if self.fault == "500":
            raise ServerFault("server error")
        if self.fault == "529":
            raise OverloadedFault("overloaded")
        if self.fault == "401":
            raise AuthFault("unauthorized")
        if self.fault == "timeout":
            raise TimeoutFault("timeout")
        text = self._derive(prompt)
        return text, Usage(input_tokens=len(prompt), output_tokens=len(text))

    async def stream(self, prompt: str) -> AsyncIterator[TokenChunk]:
        """Yield deterministic token chunks."""
        text, _ = self.complete(prompt)
        for i, ch in enumerate(text):
            yield TokenChunk(token=ch, seq=i)

    def count_tokens(self, text: str) -> int:
        return len(text)


class MockFault(Exception):
    """Base for injected mock faults."""


class RateLimitedFault(MockFault):
    pass


class ServerFault(MockFault):
    pass


class OverloadedFault(MockFault):
    pass


class AuthFault(MockFault):
    pass


class TimeoutFault(MockFault):
    pass
