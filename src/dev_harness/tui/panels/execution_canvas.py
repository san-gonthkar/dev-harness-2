"""Execution canvas panel — token stream + test sparkline (V11 task 7.3).

``#execution-canvas`` renders a scrolling token stream (a ``RichLog``) and a
test-progress ``Sparkline``. It is pure rendering: no engine logic, no
``tui/ -> engine/`` import. Envelopes arrive via a :class:`~dev_harness.tui.bridge.Bridge`
whose handlers run on the Textual UI thread, so the panel writes directly.

Reassembly contract (7.B): concatenating the received tokens in payload
``seq`` order reproduces the original stream. Duplicate ``seq`` values are
last-write-wins; tokens are ordered by ``seq`` regardless of arrival order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import RichLog, Sparkline

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import (
    AgentTokenStreamPayload,
    Envelope,
    TestProgressPayload,
)
from dev_harness.observability.redact import redact

if TYPE_CHECKING:
    from dev_harness.tui.bridge import Bridge

#: Log widget id for the token stream.
CANVAS_LOG_ID = "#canvas-log"
#: Sparkline widget id for the test-progress trace.
CANVAS_SPARKLINE_ID = "#canvas-sparkline"


class ExecutionCanvas(Vertical):
    """Token stream (RichLog) + test-progress sparkline for ``#execution-canvas``."""

    DEFAULT_CSS = """
    ExecutionCanvas {
        layout: vertical;
    }
    ExecutionCanvas > #canvas-log {
        height: 1fr;
        width: 1fr;
    }
    ExecutionCanvas > #canvas-sparkline {
        height: 3;
        width: 1fr;
    }
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._log = RichLog(id="canvas-log", wrap=False, highlight=False, markup=False)
        self._sparkline = Sparkline(id="canvas-sparkline")
        #: seq -> token, ordered reassembly is derived from these keys.
        self._tokens: dict[int, str] = {}
        #: One sample per TEST_PROGRESS event.
        self._samples: list[float] = []

    def compose(self) -> ComposeResult:
        """Yield the log and the sparkline as vertical children."""
        yield self._log
        yield self._sparkline

    def on_token(self, payload: AgentTokenStreamPayload) -> None:
        """Append a streamed token; ``seq`` drives the reassembly order.

        The token is redacted (V11 9.7) before it is stored or written, so a
        secret never reaches the RichLog buffer.
        """
        token = redact(payload.token)
        self._tokens[payload.seq] = token
        if self.is_mounted:
            self._log.write(token)

    def on_test_progress(self, payload: TestProgressPayload) -> None:
        """Push a progress sample; ``total == 0`` yields ``0.0`` (no division)."""
        value = payload.passed / payload.total if payload.total else 0.0
        self._samples.append(value)
        if self.is_mounted:
            self._sparkline.data = list(self._samples)

    def bind(self, bridge: Bridge) -> None:
        """Register this panel's handlers with a bridge (callbacks run on the UI thread)."""
        bridge.on(EventType.AGENT_TOKEN_STREAM, self._handle_token)
        bridge.on(EventType.TEST_PROGRESS, self._handle_progress)

    def _handle_token(self, env: Envelope) -> None:
        payload = env.payload
        if isinstance(payload, AgentTokenStreamPayload):
            self.on_token(payload)

    def _handle_progress(self, env: Envelope) -> None:
        payload = env.payload
        if isinstance(payload, TestProgressPayload):
            self.on_test_progress(payload)

    @property
    def rendered_text(self) -> str:
        """The concatenated token text in ``seq`` order (reassembled stream)."""
        return "".join(self._tokens[seq] for seq in sorted(self._tokens))

    @property
    def buffer_text(self) -> str:
        """The RichLog widget buffer's visible text (V11 9.7 sink (a)).

        Reads :attr:`RichLog.lines` (``Strip`` rows) and joins their ``text``,
        so a secret that reached the widget would be observable here.
        """
        return "\n".join(strip.text for strip in self._log.lines)

    @property
    def sparkline_len(self) -> int:
        """Number of ``TEST_PROGRESS`` samples accepted."""
        return len(self._samples)
