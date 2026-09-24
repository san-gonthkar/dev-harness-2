"""Execution canvas panel tests (V11 task 7.3).

Unit tests drive the handlers directly; the integration test mounts the panel
under a real ``HermesApp``, pushes 500 ``AGENT_TOKEN_STREAM`` envelopes through
a ``Bridge`` backed by a ``BackpressureQueue``, and asserts exact reassembly.
No ``time.sleep`` — waits are bounded by a deadline and settle via
``pilot.pause``.
"""

from __future__ import annotations

import threading
import time

import pytest
from textual.widgets import RichLog, Sparkline

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import AgentTokenStreamPayload, Envelope
from dev_harness.contracts.events import TestProgressPayload as ProgressPayload
from dev_harness.ipc.queue import BackpressureQueue
from dev_harness.tui.app import EXECUTION_CANVAS_ID, HermesApp
from dev_harness.tui.bridge import Bridge
from dev_harness.tui.panels.execution_canvas import (
    CANVAS_LOG_ID,
    CANVAS_SPARKLINE_ID,
    ExecutionCanvas,
)

SIZE = (100, 30)
#: Bounded wait for the reader thread to drain the queue.
DRAIN_DEADLINE_S = 30.0


def token_env(seq: int, token: str) -> Envelope:
    return Envelope(
        type=EventType.AGENT_TOKEN_STREAM,
        seq=seq,
        payload=AgentTokenStreamPayload(
            type="AGENT_TOKEN_STREAM", seq=seq, token=token
        ),
    )


def progress_env(seq: int, passed: int, total: int) -> Envelope:
    return Envelope(
        type=EventType.TEST_PROGRESS,
        seq=seq,
        payload=ProgressPayload(
            type="TEST_PROGRESS",
            chunk_id="c1",
            passed=passed,
            failed=total - passed,
            total=total,
        ),
    )


# --- unit -------------------------------------------------------------------


@pytest.mark.unit
def test_empty_initial_state() -> None:
    """A fresh canvas reassembles to '' and has no sparkline samples."""
    canvas = ExecutionCanvas()
    assert canvas.rendered_text == ""
    assert canvas.sparkline_len == 0


@pytest.mark.unit
def test_on_token_appends_and_orders_by_seq() -> None:
    """Tokens reassemble in ``seq`` order regardless of arrival order."""
    canvas = ExecutionCanvas()
    canvas.on_token(AgentTokenStreamPayload(type="AGENT_TOKEN_STREAM", seq=2, token="c"))
    canvas.on_token(AgentTokenStreamPayload(type="AGENT_TOKEN_STREAM", seq=0, token="a"))
    canvas.on_token(AgentTokenStreamPayload(type="AGENT_TOKEN_STREAM", seq=1, token="b"))
    assert canvas.rendered_text == "abc"


@pytest.mark.unit
def test_on_test_progress_grows_sparkline() -> None:
    """Each ``TEST_PROGRESS`` adds exactly one sparkline sample."""
    canvas = ExecutionCanvas()
    for i in range(5):
        canvas.on_test_progress(
            ProgressPayload(
                type="TEST_PROGRESS",
                chunk_id=f"c{i}",
                passed=i,
                failed=0,
                total=5,
            )
        )
    assert canvas.sparkline_len == 5


# --- negative ---------------------------------------------------------------


@pytest.mark.negative
def test_test_progress_zero_total_does_not_divide_by_zero() -> None:
    """``total == 0`` yields a 0.0 sample rather than raising."""
    canvas = ExecutionCanvas()
    canvas.on_test_progress(
        ProgressPayload(
            type="TEST_PROGRESS", chunk_id="c0", passed=0, failed=0, total=0
        )
    )
    assert canvas.sparkline_len == 1


@pytest.mark.negative
def test_duplicate_seq_is_last_write_wins() -> None:
    """A duplicate ``seq`` overwrites, so reassembly stays crash-free."""
    canvas = ExecutionCanvas()
    canvas.on_token(AgentTokenStreamPayload(type="AGENT_TOKEN_STREAM", seq=0, token="a"))
    canvas.on_token(AgentTokenStreamPayload(type="AGENT_TOKEN_STREAM", seq=0, token="z"))
    assert canvas.rendered_text == "z"


# --- integration ------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_500_tokens_reassemble_exactly() -> None:
    """500 cross-thread tokens reassemble exactly; sparkline length == progress count."""
    tokens = [f"t{i}-" for i in range(500)]
    expected = "".join(tokens)
    progress_count = 7

    app = HermesApp()
    queue = BackpressureQueue()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        canvas = app.query_one(EXECUTION_CANVAS_ID, ExecutionCanvas)
        assert app.query_one(CANVAS_LOG_ID, RichLog) is not None
        assert app.query_one(CANVAS_SPARKLINE_ID, Sparkline) is not None

        bridge = Bridge(app, queue=queue)
        canvas.bind(bridge)
        bridge.start()

        produced = threading.Event()

        def produce() -> None:
            for i, token in enumerate(tokens):
                queue.put(token_env(i, token))
            for i in range(progress_count):
                queue.put(progress_env(500 + i, passed=i, total=progress_count))
            produced.set()

        producer = threading.Thread(target=produce, name="producer")
        producer.start()

        deadline = time.monotonic() + DRAIN_DEADLINE_S
        while not produced.is_set() and time.monotonic() < deadline:
            await pilot.pause()
        while bridge.applied < len(tokens) + progress_count and time.monotonic() < deadline:
            await pilot.pause()

        bridge.stop(timeout=2.0)
        producer.join(timeout=2.0)

        assert queue.dropped_frames == 0
        assert canvas.rendered_text == expected
        assert canvas.sparkline_len == progress_count
