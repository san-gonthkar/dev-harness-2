"""Critic-bar panel — the operator's command surface (V11 task 7.6).

``#critic-bar`` is where the operator issues critic commands. It renders a
free-text ``Input`` (``#critic-input``) and five buttons: PAUSE, RESUME, STOP
and the HITL Approve/Reject pair. Pressing a button *publishes* exactly one
``INTERRUPT_REQUEST`` envelope carrying a :class:`CriticCommand` and a
``reason`` (the Input value when non-empty).

Scope boundary (7.D): the HITL buttons publish events that nothing yet consumes
— publishing is the whole job; this panel wires no consumer. It is pure
publication: no engine logic, no ``tui/ -> engine/`` import. The publish sink is
injected (a ``Callable[[Envelope], None]``); when it is ``None`` the emitted
envelopes are buffered in :attr:`emitted` so tests can assert without a socket.

Button -> command mapping (only real :class:`CriticCommand` members):

* ``#btn-pause``   -> ``PAUSE``
* ``#btn-resume``  -> ``RESUME``
* ``#btn-stop``    -> ``STOP``
* ``#btn-approve`` -> ``RESUME`` (HITL approval resumes the paused pipeline)
* ``#btn-reject``  -> ``STOP``   (HITL rejection halts the pipeline)
"""

from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Input

from dev_harness.contracts.enums import CriticCommand, EventType
from dev_harness.contracts.events import Envelope, InterruptRequestPayload

#: Input widget id for the free-text reason.
CRITIC_INPUT_ID = "#critic-input"
#: Button ids for the command surface.
BTN_PAUSE_ID = "#btn-pause"
BTN_RESUME_ID = "#btn-resume"
BTN_STOP_ID = "#btn-stop"
BTN_APPROVE_ID = "#btn-approve"
BTN_REJECT_ID = "#btn-reject"

#: Button id -> the command it publishes. Approve/Reject reuse RESUME/STOP
#: because ``CriticCommand`` has no distinct HITL member (0.3 enum).
BUTTON_COMMANDS: dict[str, CriticCommand] = {
    BTN_PAUSE_ID: CriticCommand.PAUSE,
    BTN_RESUME_ID: CriticCommand.RESUME,
    BTN_STOP_ID: CriticCommand.STOP,
    BTN_APPROVE_ID: CriticCommand.RESUME,
    BTN_REJECT_ID: CriticCommand.STOP,
}

#: A publish sink; receives each emitted envelope.
PublishSink = Callable[[Envelope], None]


class CriticBar(Horizontal):
    """Operator command surface for ``#critic-bar`` (publishes ``INTERRUPT_REQUEST``)."""

    DEFAULT_CSS = """
    CriticBar {
        layout: horizontal;
        height: 3;
    }
    CriticBar > #critic-input {
        width: 1fr;
    }
    """

    def __init__(
        self,
        *,
        publish: PublishSink | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._publish = publish
        #: Envelopes published so far (buffered when no sink is injected).
        self._emitted: list[Envelope] = []
        #: The command from the most recent emission; ``None`` before any.
        self._last_command: CriticCommand | None = None
        self._input = Input(placeholder="reason", id="critic-input")

    def compose(self) -> ComposeResult:
        """Yield the reason input and the five command buttons."""
        yield self._input
        yield Button("PAUSE", id="btn-pause")
        yield Button("RESUME", id="btn-resume")
        yield Button("STOP", id="btn-stop")
        yield Button("Approve", id="btn-approve")
        yield Button("Reject", id="btn-reject")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Publish the command mapped to the pressed button (unknown ids are ignored)."""
        command = BUTTON_COMMANDS.get(f"#{event.button.id}")
        if command is None:
            return
        self.emit(command, self._input.value)

    def emit(self, command: CriticCommand, reason: str = "") -> Envelope:
        """Build and publish exactly one ``INTERRUPT_REQUEST`` envelope; return it."""
        envelope = Envelope(
            type=EventType.INTERRUPT_REQUEST,
            payload=InterruptRequestPayload(
                type="INTERRUPT_REQUEST", command=command, reason=reason
            ),
        )
        self._emitted.append(envelope)
        self._last_command = command
        if self._publish is not None:
            self._publish(envelope)
        return envelope

    @property
    def emitted(self) -> list[Envelope]:
        """The envelopes published so far, in order."""
        return self._emitted

    @property
    def last_command(self) -> CriticCommand | None:
        """The command from the most recent emission (``None`` before any)."""
        return self._last_command
