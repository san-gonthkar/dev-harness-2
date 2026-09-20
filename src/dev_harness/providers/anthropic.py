"""Anthropic adapter: non-streaming + SSE streaming (V11 3.4)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from dev_harness.contracts.llm import Message, TokenChunk, Usage
from dev_harness.providers._common import messages_to_payload, raise_for_status
from dev_harness.providers.errors import map_exception


class AnthropicClient:
    """Anthropic Messages API client (satisfies LLMClient)."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.anthropic.com",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        """Non-streaming completion."""
        payload = {
            "model": model or "claude-3-5-sonnet-20241022",
            "max_tokens": 4096,
            "messages": messages_to_payload(messages),
        }
        try:
            resp = await self._client.post(
                f"{self.base_url}/v1/messages", json=payload, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise map_exception(exc) from exc
        raise_for_status(resp)
        data = resp.json()
        text = "".join(
            b.get("text", "")
            for b in data.get("content", [])
            if b.get("type") == "text"
        )
        usage = data.get("usage", {})
        return text, Usage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    async def stream(
        self, messages: list[Message], *, model: str | None = None
    ) -> AsyncIterator[TokenChunk]:
        """SSE streaming completion."""
        payload = {
            "model": model or "claude-3-5-sonnet-20241022",
            "max_tokens": 4096,
            "stream": True,
            "messages": messages_to_payload(messages),
        }
        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/v1/messages/stream",
                json=payload,
                headers=self._headers(),
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
                    delta = chunk.get("delta", {})
                    text = delta.get("text", "")
                    if text:
                        yield TokenChunk(token=text, seq=seq)
                        seq += 1
        except httpx.HTTPError as exc:
            raise map_exception(exc) from exc

    def count_tokens(self, text: str) -> int:
        """Estimate tokens as 4 chars/token (exact tokenizer is provider-side)."""
        return max(1, len(text) // 4)
