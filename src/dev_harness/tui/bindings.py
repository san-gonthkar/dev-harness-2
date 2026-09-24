"""Keybindings and the confirm-quit modal (V11 task 7.9).

Two operator bindings override Textual's defaults:

* ``ctrl+c`` is **priority-bound to PAUSE** — it must never quit the app. It
  emits exactly one ``INTERRUPT_REQUEST{PAUSE}`` through the critic bar (7.6)
  and the app keeps running.
* ``ctrl+q`` opens :class:`ConfirmQuitScreen`; only a confirmed *Yes* exits.

``tui/`` never imports ``engine/``: the PAUSE emission reuses the 7.6
``INTERRUPT_REQUEST`` contract via the mounted ``CriticBar``.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

#: Confirm-modal button ids.
CONFIRM_YES_ID = "confirm-yes"
CONFIRM_NO_ID = "confirm-no"

#: The two operator bindings attached to :class:`~dev_harness.tui.app.HermesApp`.
#: ``ctrl+c`` is priority so it wins over Textual's system ``help_quit`` binding.
HERMES_BINDINGS: list[Binding] = [
    Binding("ctrl+c", "pause", "Pause", priority=True),
    Binding("ctrl+q", "request_quit", "Quit"),
]


class ConfirmQuitScreen(ModalScreen[bool]):
    """A Yes/No modal; ``dismiss(True)`` on Yes, ``dismiss(False)`` on No."""

    DEFAULT_CSS = """
    ConfirmQuitScreen {
        align: center middle;
    }
    ConfirmQuitScreen > #confirm-dialog {
        width: 40;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    ConfirmQuitScreen > #confirm-dialog > #confirm-buttons {
        width: 100%;
        height: auto;
        align: center middle;
    }
    ConfirmQuitScreen > #confirm-dialog > #confirm-buttons > Button {
        width: auto;
        min-width: 8;
    }
    """

    BINDINGS: ClassVar[list] = [  # type: ignore[type-arg]  # textual's BindingType is unexported
        Binding("escape", "dismiss(False)", "Cancel")
    ]

    def compose(self) -> ComposeResult:
        """Yield the prompt and the Yes/No buttons."""
        with Vertical(id="confirm-dialog"):
            yield Label("Quit Hermes?")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", id=CONFIRM_YES_ID, variant="error")
                yield Button("No", id=CONFIRM_NO_ID, variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dismiss with ``True`` for Yes, ``False`` for No."""
        self.dismiss(event.button.id == CONFIRM_YES_ID)
