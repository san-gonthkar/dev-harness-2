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
    requests: list[dict[str, object]] = field(default_factory=list)

    async def __call__(
        self, scope: dict[str, object], receive: object, send: object
    ) -> None:
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
        if scope["path"].endswith("/stream"):
            await _send_sse(send, text, scope["path"])
            return
        await _send_provider_response(send, scope["path"], text, prompt)

    def client(self) -> httpx.AsyncClient:
        """An httpx client wired to this server via ASGITransport (no TCP)."""
        transport = httpx.ASGITransport(app=self)
        return httpx.AsyncClient(transport=transport, base_url="http://fake")


async def _read_body(receive: object) -> dict[str, object]:
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


def _extract_prompt(body: dict[str, object]) -> str:
    """Extract the prompt from a provider request body."""
    if isinstance(body, dict):
        for key in ("prompt", "messages", "input"):
            if key in body:
                value = body[key]
                if isinstance(value, list):
                    return json.dumps(value)
                return str(value)
    return json.dumps(body)


async def _send_json(send: object, status: int, payload: dict[str, object]) -> None:
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


async def _send_sse(send: object, text: str, path: str = "") -> None:
    """Send an SSE stream of character chunks in the provider's shape."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        }
    )
    for ch in text:
        if "/chat/completions" in path:
            # OpenRouter shape: choices[0].delta.content
            payload = json.dumps({"choices": [{"delta": {"content": ch}}]}).encode(
                "utf-8"
            )
        elif "/api/chat" in path:
            # Ollama shape: message.content
            payload = json.dumps(
                {"message": {"role": "assistant", "content": ch}}
            ).encode("utf-8")
        else:
            # Anthropic shape: delta.text
            payload = json.dumps({"delta": {"text": ch}}).encode("utf-8")
        await send(
            {
                "type": "http.response.body",
                "body": b"data: " + payload + b"\n\n",
                "more_body": True,
            }
        )
    await send({"type": "http.response.body", "body": b"data: [DONE]\n\n"})


async def _send_provider_response(
    send: object, path: str, text: str, prompt: str
) -> None:
    """Send a provider-appropriate JSON response based on the request path."""
    if "/v1/messages" in path:
        # Anthropic shape.
        payload = {
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": len(prompt), "output_tokens": len(text)},
        }
    elif "/api/chat" in path:
        # Ollama shape.
        payload = {"message": {"role": "assistant", "content": text}}
    else:
        # OpenRouter shape.
        payload = {
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": len(prompt), "completion_tokens": len(text)},
        }
    await _send_json(send, 200, payload)
