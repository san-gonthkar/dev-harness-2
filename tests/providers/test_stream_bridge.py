"""Stream bridge tests (V11 3.9)."""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope
from dev_harness.contracts.llm import TokenChunk
from dev_harness.providers.stream_bridge import StreamBridge

pytestmark = pytest.mark.unit


async def _chunks(n: int):
    """An async generator of n token chunks."""
    for i in range(n):
        yield TokenChunk(token=f"t{i}", seq=i)


@pytest.mark.asyncio
async def test_1000_chunks_strictly_increasing_no_gaps() -> None:
    emitted: list[Envelope] = []
    bridge = StreamBridge(emit=emitted.append)
    usage = await bridge.forward(_chunks(1000))
    token_envs = [e for e in emitted if e.type == EventType.AGENT_TOKEN_STREAM]
    assert len(token_envs) == 1000
    seqs = [e.payload.seq for e in token_envs]
    assert seqs == list(range(1000))  # strictly increasing, 0 gaps
    assert usage.output_tokens == 1000


@pytest.mark.asyncio
async def test_terminal_envelope_carries_usage() -> None:
    emitted: list[Envelope] = []
    bridge = StreamBridge(emit=emitted.append)
    await bridge.forward(_chunks(5))
    terminal = emitted[-1]
    assert terminal.type == EventType.METRICS_UPDATE
    assert terminal.payload.tpm_burn == 5


@pytest.mark.asyncio
async def test_agent_name_in_payload() -> None:
    emitted: list[Envelope] = []
    bridge = StreamBridge(emit=emitted.append)
    await bridge.forward(_chunks(2), agent="architect")
    assert emitted[0].payload.agent == "architect"


@pytest.mark.asyncio
async def test_empty_stream() -> None:
    emitted: list[Envelope] = []
    bridge = StreamBridge(emit=emitted.append)

    async def empty():
        if False:
            yield TokenChunk("", 0)

    usage = await bridge.forward(empty())
    assert usage.output_tokens == 0
    assert emitted[-1].type == EventType.METRICS_UPDATE
