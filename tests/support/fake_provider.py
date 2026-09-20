"""FakeProviderServer: in-process ASGI fake LLM provider (V11 0.13).

Served over httpx.ASGITransport - no TCP bind, which is what makes the
--disable-socket guarantee hold. Supports scripted completions, SSE streaming,
and injectable 429/5xx/timeout faults.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import httpx


@dataclass
class FakeProviderServer:
    """An ASGI fake provider with scripted responses and fault injection."""

    scripted: dict[str, str] = field(default_factory=dict)
    fault: str | None = None  # "429" | "500" | "529" | "401" | "timeout"
    requests: list[dict] = field(default_factory=list)

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        """ASGI application entry point."""
        if scope["type"] != "http":
            return
        body = await _read_body(receive)
        self.requests.append({"path": scope["path"], "body": body})
        if self.fault == "429":
            await _send_json(send, 429, {"error": "rate_limit", "retry_after": 30})
            return
        if self.fault == "500":
            await _send_json(send, 500, {"error": "internal"})
            return
        if self.fault == "529":
            await _send_json(send, 529, {"error": "overloaded"})
            return
        if self.fault == "401":
            await _send_json(send, 401, {"error": "unauthorized"})
            return
        if self.fault == "timeout":
            await asyncio.sleep(3600)  # never respond
            return
        # Normal completion: echo the scripted text or a deterministic default.
        prompt = _extract_prompt(body)
        text = self.scripted.get(prompt, f"fake-completion-{len(self.requests)}")
        await _send_json(send, 200, {"content": text, "usage": {"input_tokens": len(prompt), "output_tokens": len(text)}})

    def client(self) -> httpx.AsyncClient:
        """An httpx client wired to this server via ASGITransport (no TCP)."""
        transport = httpx.ASGITransport(app=self)
        return httpx.AsyncClient(transport=transport, base_url="http://fake")


async def _read_body(receive: object) -> dict:
    """Read the request body from the ASGI receive callable."""
    message = await receive()
    body = message.get("body", b"")
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def _extract_prompt(body: dict) -> str:
    """Extract the prompt from a provider request body."""
    if isinstance(body, dict):
        for key in ("prompt", "messages", "input"):
            if key in body:
                value = body[key]
                if isinstance(value, list):
                    return json.dumps(value)
                return str(value)
    return json.dumps(body)


async def _send_json(send: object, status: int, payload: dict) -> None:
    """Send a JSON response via the ASGI send callable."""
    data = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": data})
