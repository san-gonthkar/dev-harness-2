"""Streaming -> IPC bridge emitting sequenced AGENT_TOKEN_STREAM (V11 3.9).

Consumes an async token stream and emits Envelope(AGENT_TOKEN_STREAM) with
strictly increasing seq. The terminal envelope carries the final Usage.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import (
    AgentTokenStreamPayload,
    Envelope,
    MetricsUpdatePayload,
)
from dev_harness.contracts.llm import TokenChunk, Usage

EmitFn = Callable[[Envelope], None]


class StreamBridge:
    """Bridges an async token stream to sequenced IPC envelopes."""

    def __init__(self, emit: EmitFn) -> None:
        self._emit = emit
        self._seq = 0

    async def forward(
        self, chunks: AsyncIterator[TokenChunk], *, agent: str = ""
    ) -> Usage:
        """Forward chunks as AGENT_TOKEN_STREAM envelopes; return final Usage.

        The terminal envelope is a METRICS_UPDATE carrying the final Usage.
        """
        output_tokens = 0
        async for chunk in chunks:
            self._emit(
                Envelope(
                    type=EventType.AGENT_TOKEN_STREAM,
                    payload=AgentTokenStreamPayload(
                        type="AGENT_TOKEN_STREAM",
                        seq=self._seq,
                        token=chunk.token,
                        agent=agent,
                    ),
                )
            )
            self._seq += 1
            output_tokens += 1
        usage = Usage(input_tokens=0, output_tokens=output_tokens)
        self._emit(
            Envelope(
                type=EventType.METRICS_UPDATE,
                payload=MetricsUpdatePayload(
                    type="METRICS_UPDATE",
                    p50_latency_ms=0.0,
                    p95_latency_ms=0.0,
                    tpm_burn=output_tokens,
                    cumulative_usd=0.0,
                ),
            )
        )
        return usage
