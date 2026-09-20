"""Additional providers coverage: CLI real paths, adapter error branches, fake faults."""

from __future__ import annotations

import sys

import pytest

from dev_harness.contracts.llm import Message
from dev_harness.providers import cli
from dev_harness.providers._fake import FakeProviderServer
from dev_harness.providers.anthropic import AnthropicClient
from dev_harness.providers.ollama import OllamaClient
from dev_harness.providers.openrouter import OpenRouterClient

pytestmark = pytest.mark.unit


class TestCliRealPaths:
    @pytest.mark.asyncio
    async def test_complete_anthropic_fake(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = await cli._complete("anthropic", "ping", True)
        assert rc == 0
        assert "completion:" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_complete_openrouter_fake(self) -> None:
        assert await cli._complete("openrouter", "ping", True) == 0

    @pytest.mark.asyncio
    async def test_complete_ollama_fake(self) -> None:
        assert await cli._complete("ollama", "ping", True) == 0

    @pytest.mark.asyncio
    async def test_stream_anthropic_fake(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = await cli._stream("anthropic", "ping", True)
        assert rc == 0
        assert "streamed:" in capsys.readouterr().out

    def test_make_client_real_anthropic(self) -> None:
        client = cli._make_client("anthropic", False)
        assert isinstance(client, AnthropicClient)

    def test_make_client_real_openrouter(self) -> None:
        client = cli._make_client("openrouter", False)
        assert isinstance(client, OpenRouterClient)

    def test_make_client_real_ollama(self) -> None:
        client = cli._make_client("ollama", False)
        assert isinstance(client, OllamaClient)

    def test_main_stream_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_stream(provider: str, prompt: str, fake: bool) -> int:
            return 0

        monkeypatch.setattr(cli, "_stream", fake_stream)
        monkeypatch.setattr(
            sys,
            "argv",
            ["cli", "stream", "--provider", "anthropic", "--prompt", "ping", "--fake"],
        )
        assert cli.main() == 0

    def test_main_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import runpy

        monkeypatch.setattr(sys, "argv", ["cli", "fault-drill", "--codes", "429"])
        with pytest.raises(SystemExit) as ei:
            runpy.run_module("dev_harness.providers.cli", run_name="__main__")
        assert ei.value.code == 0


class TestAdapterErrorBranches:
    @pytest.mark.asyncio
    async def test_anthropic_500_raises_transient(self) -> None:
        server = FakeProviderServer(fault="500")
        client = AnthropicClient("k", base_url="http://fake", client=server.client())
        from dev_harness.contracts.errors import TransientError

        with pytest.raises(TransientError):
            await client.complete([Message(role="user", content="hi")])
        await server.client().aclose()

    @pytest.mark.asyncio
    async def test_anthropic_529_raises_overloaded(self) -> None:
        server = FakeProviderServer(fault="529")
        client = AnthropicClient("k", base_url="http://fake", client=server.client())
        from dev_harness.contracts.errors import ProviderOverloadedError

        with pytest.raises(ProviderOverloadedError):
            await client.complete([Message(role="user", content="hi")])
        await server.client().aclose()

    @pytest.mark.asyncio
    async def test_ollama_401_raises_auth(self) -> None:
        server = FakeProviderServer(fault="401")
        client = OllamaClient(base_url="http://fake", client=server.client())
        from dev_harness.contracts.errors import AuthError

        with pytest.raises(AuthError):
            await client.complete([Message(role="user", content="hi")])
        await server.client().aclose()


class TestFakeFaults:
    @pytest.mark.asyncio
    async def test_fake_500(self) -> None:
        server = FakeProviderServer(fault="500")
        async with server.client() as client:
            resp = await client.post("/v1/complete", json={"prompt": "x"})
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_fake_529(self) -> None:
        server = FakeProviderServer(fault="529")
        async with server.client() as client:
            resp = await client.post("/v1/complete", json={"prompt": "x"})
        assert resp.status_code == 529

    @pytest.mark.asyncio
    async def test_fake_non_http_scope(self) -> None:
        """A non-HTTP scope is ignored."""
        server = FakeProviderServer()

        async def receive() -> dict:
            return {}

        async def send(msg: dict) -> None:
            pass

        await server({"type": "websocket"}, receive, send)  # type: ignore[arg-type]
        assert server.requests == []

    @pytest.mark.asyncio
    async def test_fake_raw_body(self) -> None:
        """A non-JSON body is captured as raw."""
        server = FakeProviderServer()
        async with server.client() as client:
            resp = await client.post("/v1/complete", content=b"not-json")
        assert resp.status_code == 200
        assert server.requests[0]["body"] == {"raw": "not-json"}
