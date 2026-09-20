"""Shared httpx plumbing for the provider adapters (V11 3.4-3.6)."""

from __future__ import annotations

import httpx

from dev_harness.contracts.llm import Message
from dev_harness.providers.errors import map_exception


def messages_to_payload(messages: list[Message]) -> list[dict[str, str]]:
    """Convert neutral Messages to a provider payload list."""
    return [{"role": m.role, "content": m.content} for m in messages]


def raise_for_status(response: httpx.Response) -> None:
    """Raise the typed provider error for a non-2xx response."""
    if response.status_code >= 400:
        raise map_exception(
            httpx.HTTPStatusError(
                f"{response.status_code} {response.reason_phrase}",
                request=response.request,
                response=response,
            )
        )
