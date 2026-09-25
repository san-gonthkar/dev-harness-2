"""Canvas-level secret redaction tests (V11 task 9.7).

Acceptance (9.B) is exact: a secret key is **absent from all three sinks
simultaneously** — (a) the widget buffer (the canvas's rendered text), (b) the
logs, and (c) the run artifacts. Each sink is grepped for the raw key and must
come back empty.

The render boundary (``tui/render.py``) and the canvas token handler route text
through :func:`dev_harness.observability.redact.redact`; the log and artifact
paths already redact (0.14/0.15) and are verified here rather than assumed.

No ``time.sleep`` — the integration wait is bounded by a deadline and settles
via ``pilot.pause``. No network.
"""

from __future__ import annotations

import io
import json
import logging
import time
from pathlib import Path

import pytest
from rich.console import Console

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import AgentTokenStreamPayload, Envelope
from dev_harness.ipc.queue import BackpressureQueue
from dev_harness.observability.artifacts import RunArtifactStore
from dev_harness.observability.logging import JsonFormatter, RedactFilter
from dev_harness.observability.redact import REDACTED, contains_secret, redact
from dev_harness.tui.app import EXECUTION_CANVAS_ID, HermesApp
from dev_harness.tui.bridge import Bridge
from dev_harness.tui.panels.execution_canvas import ExecutionCanvas
from dev_harness.tui.render import render_diff, render_markdown, safe_text

#: A fake Anthropic-style key that matches the redaction pattern.
API_KEY = "sk-ant-api03-ABCDEF1234567890XYZ"
#: Bounded wait for the bridge reader thread to drain the queue.
DRAIN_DEADLINE_S = 30.0
SIZE = (100, 30)


def token_env(seq: int, token: str) -> Envelope:
    return Envelope(
        type=EventType.AGENT_TOKEN_STREAM,
        seq=seq,
        payload=AgentTokenStreamPayload(
            type="AGENT_TOKEN_STREAM", seq=seq, token=token
        ),
    )


def _log_sink(text: str) -> str:
    """Run ``text`` through the structured logger and return the emitted line."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(correlation_id="redact9.7"))
    handler.addFilter(RedactFilter())
    logger = logging.getLogger("test.canvas_redaction")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("%s", text)
    return stream.getvalue()


# --- unit -------------------------------------------------------------------


@pytest.mark.unit
def test_render_boundary_redacts_secret() -> None:
    """``safe_text``/``render_markdown``/``render_diff`` mask a key in their output."""
    assert not contains_secret(safe_text(f"token={API_KEY}").plain, API_KEY)
    assert REDACTED in safe_text(f"token={API_KEY}").plain

    console = Console(record=True, width=80)
    console.print(render_markdown(f"key {API_KEY} here"))
    assert not contains_secret(console.export_text(), API_KEY)

    assert not contains_secret(render_diff(f"+key {API_KEY}").plain, API_KEY)


@pytest.mark.unit
def test_log_and_artifact_sinks_redact_secret(tmp_path: Path) -> None:
    """The log line and the artifact file both mask the key (sinks b and c)."""
    line = _log_sink(f"using key {API_KEY}")
    assert not contains_secret(line, API_KEY)
    assert REDACTED in json.loads(line)["message"]

    store = RunArtifactStore(tmp_path / "run.jsonl")
    store.append({"message": f"using key {API_KEY}"})
    artifact_text = (tmp_path / "run.jsonl").read_text(encoding="utf-8")
    assert not contains_secret(artifact_text, API_KEY)
    assert REDACTED in artifact_text


# --- negative ---------------------------------------------------------------


@pytest.mark.negative
def test_contains_secret_detects_raw_but_not_redacted() -> None:
    """``contains_secret`` is a true grep: raw key present, redacted key absent."""
    assert contains_secret(f"x {API_KEY} y", API_KEY)
    assert not contains_secret(redact(f"x {API_KEY} y"), API_KEY)


# --- integration ------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_key_absent_from_all_three_sinks(tmp_path: Path) -> None:
    """A streamed key is absent from the widget buffer, the logs, and the artifacts."""
    tokens = ["thinking... ", f"key={API_KEY}", " done"]
    stream_text = "".join(tokens)

    app = HermesApp()
    queue = BackpressureQueue()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        canvas = app.query_one(EXECUTION_CANVAS_ID, ExecutionCanvas)

        bridge = Bridge(app, queue=queue)
        canvas.bind(bridge)
        bridge.start()

        for seq, token in enumerate(tokens):
            queue.put(token_env(seq, token))

        deadline = time.monotonic() + DRAIN_DEADLINE_S
        while bridge.applied < len(tokens) and time.monotonic() < deadline:
            await pilot.pause()
        bridge.stop(timeout=2.0)

        # (a) widget buffer — the canvas's rendered text.
        buffer_text = canvas.buffer_text
        assert not contains_secret(buffer_text, API_KEY)
        assert not contains_secret(canvas.rendered_text, API_KEY)
        assert REDACTED in buffer_text

    # (b) logs.
    log_text = _log_sink(stream_text)
    assert not contains_secret(log_text, API_KEY)

    # (c) run artifacts.
    store = RunArtifactStore(tmp_path / "run.jsonl")
    store.append({"message": stream_text})
    artifact_text = (tmp_path / "run.jsonl").read_text(encoding="utf-8")
    assert not contains_secret(artifact_text, API_KEY)

    # All three greps empty, simultaneously.
    assert API_KEY not in buffer_text
    assert API_KEY not in log_text
    assert API_KEY not in artifact_text