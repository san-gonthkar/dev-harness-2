"""Ollama adapter: /api/chat streaming, num_ctx/keep_alive, cold-start (V11 3.6)."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

import httpx

from dev_harness.contracts.llm import Message, TokenChunk, Usage
from dev_harness.providers._common import messages_to_payload, raise_for_status
from dev_harness.providers.errors import map_exception

COLD_START_TTFB = 5.0  # seconds


class OllamaClient:
    """Ollama /api/chat client (satisfies LLMClient).

    num_ctx and keep_alive are sent in the request body. A first-token latency
    above COLD_START_TTFB marks chunks with model_loading=True instead of
    raising a timeout error.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        client: httpx.AsyncClient | None = None,
        num_ctx: int = 8192,
        keep_alive: str = "5m",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        """Non-streaming completion."""
        payload = {
            "model": model or "qwen2.5-coder:7b",
            "messages": messages_to_payload(messages),
            "options": {"num_ctx": self.num_ctx, "keep_alive": self.keep_alive},
        }
        try:
            resp = await self._client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise map_exception(exc) from exc
        raise_for_status(resp)
        data = resp.json()
        text = data.get("message", {}).get("content", "")
        return text, Usage(input_tokens=0, output_tokens=len(text) // 4)

    async def stream(
        self, messages: list[Message], *, model: str | None = None
    ) -> AsyncIterator[TokenChunk]:
        """Streaming completion with cold-start detection."""
        payload = {
            "model": model or "qwen2.5-coder:7b",
            "messages": messages_to_payload(messages),
            "stream": True,
            "options": {"num_ctx": self.num_ctx, "keep_alive": self.keep_alive},
        }
        start = time.monotonic()
        first = True
        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/api/chat/stream", json=payload
            ) as resp:
                raise_for_status(resp)
                seq = 0
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    text = chunk.get("message", {}).get("content", "")
                    if text:
                        loading = first and (time.monotonic() - start) > COLD_START_TTFB
                        yield TokenChunk(token=text, seq=seq, model_loading=loading)
                        seq += 1
                        first = False
        except httpx.HTTPError as exc:
            raise map_exception(exc) from exc

    def count_tokens(self, text: str) -> int:
        """Estimate tokens as 4 chars/token."""
        return max(1, len(text) // 4)
