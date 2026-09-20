"""OpenRouter adapter with model-name namespacing (V11 3.5)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from dev_harness.contracts.llm import Message, TokenChunk, Usage
from dev_harness.providers._common import messages_to_payload, raise_for_status
from dev_harness.providers.errors import map_exception


class OpenRouterClient:
    """OpenRouter chat completions client (satisfies LLMClient).

    Model names are namespaced as ``vendor/model`` (e.g. ``anthropic/claude-3.5``).
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://openrouter.ai/api",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        """Non-streaming completion. The model id is namespaced as vendor/model."""
        payload = {
            "model": model or "anthropic/claude-3.5-sonnet",
            "messages": messages_to_payload(messages),
        }
        try:
            resp = await self._client.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise map_exception(exc) from exc
        raise_for_status(resp)
        data = resp.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        return text, Usage(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )

    async def stream(
        self, messages: list[Message], *, model: str | None = None
    ) -> AsyncIterator[TokenChunk]:
        """SSE streaming completion."""
        payload = {
            "model": model or "anthropic/claude-3.5-sonnet",
            "stream": True,
            "messages": messages_to_payload(messages),
        }
        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions/stream",
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
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        yield TokenChunk(token=text, seq=seq)
                        seq += 1
        except httpx.HTTPError as exc:
            raise map_exception(exc) from exc

    def count_tokens(self, text: str) -> int:
        """Estimate tokens as 4 chars/token."""
        return max(1, len(text) // 4)
