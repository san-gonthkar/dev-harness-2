---
name: llm-providers
description: 'LLM provider adapters per the V11 plan. Use when working with providers/: LLMClient protocol, model registry, error mapping, Anthropic/OpenRouter/Ollama adapters, SSE streaming, tokenizer, and the streaming-to-IPC bridge. Covers the providers/ error-mapping contract and offline guarantee.'
user-invocable: true
---

# LLM Providers (V11 Phase 3)

## When to Use

- Working with `providers/` — base protocol, registry, errors, adapters, tokenizer, stream bridge
- Adding or fixing a provider adapter

## The Protocol (task 3.1)

`LLMClient` (runtime-checkable): `complete()`, `stream()` → `TokenChunk`, `count_tokens()`; neutral `Message`/`ToolCall`/`Usage`. All adapters satisfy it — no provider-specific call-site code.

## Error Mapping (task 3.3)

| Status | Error | retryable |
| :--- | :--- | :--- |
| 429 | `RateLimitedError` | True, `retry_after=30` |
| 529 | `ProviderOverloadedError` | True |
| 401 | `AuthError` | False |
| reset | `TransientError` | True |

`providers/errors.py` is the file downstream retry logic trusts — every mapped status code has a `negative` test.

## Adapters (tasks 3.4–3.6)

- **Anthropic**: non-streaming + SSE streaming, usage extraction. Chunks reassemble byte-identically.
- **OpenRouter**: model-name namespacing preserved in the request.
- **Ollama**: `/api/chat` streaming, `num_ctx`/`keep_alive` in the body, cold-start detection (>5s TTFB → `model_loading=True`, not a timeout error).

## Thrash Guard (task 3.7)

Serialize load-forcing requests; expose `model_loaded`; emit `MODEL_CONFIG_CHANGE` on swap. 6 interleaved requests across 2 models → ≤2 load events; never concurrent loads.

## Tokenizer (task 3.8)

Exact where available; 4-chars/token + 15% margin fallback. Estimate ≥ actual for all corpus samples (0 underestimates); overhead ≤30%.

## Stream Bridge (task 3.9)

Sequenced `AGENT_TOKEN_STREAM` envelopes: 1,000 chunks strictly increasing `seq`, 0 gaps; terminal envelope carries final `Usage`.

## Offline Guarantee (task 3.10)

The whole `providers/` suite passes with outbound TCP disabled:

```bash
pytest tests/providers -q --disable-socket --allow-unix-socket
```

`FakeProviderServer` serves over `httpx.ASGITransport` (in-process, no TCP bind).