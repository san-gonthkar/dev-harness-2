"""Providers CLI: complete, stream, fault-drill, count, swap-drill (V11 3.12).

All subcommands run against --fake (FakeProviderServer) for acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from dev_harness.contracts.llm import Message
from dev_harness.providers.base import LLMClient
from dev_harness.providers.errors import fault_table, map_status
from dev_harness.providers.tokenizer import estimate_tokens

FAKE_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def _make_client(provider: str, fake: bool) -> LLMClient:
    """Build a client for the given provider, optionally against the fake server."""
    from dev_harness.providers._fake import FakeProviderServer

    if fake:
        server = FakeProviderServer()
        client = server.client()
        if provider == "anthropic":
            from dev_harness.providers.anthropic import AnthropicClient

            return AnthropicClient("test-key", base_url="http://fake", client=client)
        if provider == "openrouter":
            from dev_harness.providers.openrouter import OpenRouterClient

            return OpenRouterClient("test-key", base_url="http://fake", client=client)
        from dev_harness.providers.ollama import OllamaClient

        return OllamaClient(base_url="http://fake", client=client)
    # Real provider (requires credentials).
    if provider == "anthropic":
        from dev_harness.providers.anthropic import AnthropicClient

        return AnthropicClient("test-key")
    if provider == "openrouter":
        from dev_harness.providers.openrouter import OpenRouterClient

        return OpenRouterClient("test-key")
    from dev_harness.providers.ollama import OllamaClient

    return OllamaClient()


async def _complete(provider: str, prompt: str, fake: bool) -> int:
    client = _make_client(provider, fake)
    text, usage = await client.complete([Message(role="user", content=prompt)])
    print(f"completion: {text}")
    print(f"usage: input={usage.input_tokens} output={usage.output_tokens}")
    return 0


async def _stream(provider: str, prompt: str, fake: bool) -> int:
    client = _make_client(provider, fake)
    chunks = [c async for c in client.stream([Message(role="user", content=prompt)])]
    reassembled = "".join(c.token for c in chunks)
    seqs = [c.seq for c in chunks]
    print(f"streamed: {reassembled}")
    print(
        f"seq: {seqs[0]}..{seqs[-1]} ({len(seqs)} chunks, 0 gaps)"
        if seqs
        else "seq: empty"
    )
    return 0


def _fault_drill(codes: str) -> int:
    """Drill each status code and print the mapped error."""
    table = {row["code"]: row for row in fault_table()}
    for code_str in codes.split(","):
        code = int(code_str.strip())
        err = map_status(code)
        row = table.get(code, {"error": type(err).__name__, "retryable": err.retryable})
        print(
            f"{code} -> {type(err).__name__} retryable={err.retryable} (table: {row['error']})"
        )
    return 0


def _count(model: str, file: str | None) -> int:
    """Count tokens for a file or a sample string."""
    if file:
        text = Path(file).read_text(encoding="utf-8")
    else:
        text = "The quick brown fox jumps over the lazy dog."
    est = estimate_tokens(text)
    print(f"model={model} chars={len(text)} estimate={est}")
    return 0


def _swap_drill(models: str, requests: int, fake: bool) -> int:
    """Drill the thrash guard with interleaved model requests."""
    from dev_harness.providers.ollama_loader import OllamaLoader

    loader = OllamaLoader()
    model_list = models.split(",")
    for i in range(requests):
        loader.ensure_loaded(model_list[i % len(model_list)])
    loader.flush()
    print(
        f"swap-drill: {requests} requests, {loader.load_events} load events, loaded={loader.model_loaded}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dev_harness.providers.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_complete = sub.add_parser("complete")
    p_complete.add_argument("--provider", required=True)
    p_complete.add_argument("--prompt", required=True)
    p_complete.add_argument("--fake", action="store_true")
    p_complete.set_defaults(func=_complete)

    p_stream = sub.add_parser("stream")
    p_stream.add_argument("--provider", required=True)
    p_stream.add_argument("--prompt", required=True)
    p_stream.add_argument("--fake", action="store_true")
    p_stream.set_defaults(func=_stream)

    p_fault = sub.add_parser("fault-drill")
    p_fault.add_argument("--codes", required=True)
    p_fault.set_defaults(func=_fault_drill)

    p_count = sub.add_parser("count")
    p_count.add_argument("--model", required=True)
    p_count.add_argument("--file", default=None)
    p_count.set_defaults(func=_count)

    p_swap = sub.add_parser("swap-drill")
    p_swap.add_argument("--models", required=True)
    p_swap.add_argument("--requests", type=int, default=6)
    p_swap.add_argument("--fake", action="store_true")
    p_swap.set_defaults(func=_swap_drill)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "complete":
        return asyncio.run(_complete(args.provider, args.prompt, args.fake))
    if args.command == "stream":
        return asyncio.run(_stream(args.provider, args.prompt, args.fake))
    if args.command == "fault-drill":
        return int(args.func(args.codes))
    if args.command == "count":
        return int(args.func(args.model, args.file))
    if args.command == "swap-drill":
        return int(args.func(args.models, args.requests, args.fake))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
