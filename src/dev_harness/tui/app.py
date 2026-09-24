"""HermesApp — the four-panel Hermes TUI shell (V11 task 7.1).

The shell owns the CSS grid and the four region placeholders. The real
panels mount their widgets into these exact IDs in tasks 7.2/7.3/7.5/7.6;
the bridge (7.7), throttle (7.8) and keybindings (7.9) attach later.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult

from dev_harness.contracts.enums import CriticCommand, PanelId
from dev_harness.contracts.events import Envelope
from dev_harness.tui.bindings import HERMES_BINDINGS, ConfirmQuitScreen
from dev_harness.tui.panels.critic_bar import CriticBar
from dev_harness.tui.panels.execution_canvas import ExecutionCanvas
from dev_harness.tui.panels.model_registry import ModelRegistry
from dev_harness.tui.panels.repo_manager import RepoManager

#: CSS grid region IDs — the four panels mount here (7.2/7.3/7.5/7.6).
REPO_MANAGER_ID = "#repo-manager"
EXECUTION_CANVAS_ID = "#execution-canvas"
MODEL_REGISTRY_ID = "#model-registry"
CRITIC_BAR_ID = "#critic-bar"

#: PanelId -> CSS selector for the region each panel owns.
PANEL_REGIONS: dict[PanelId, str] = {
    PanelId.WORKSPACE: REPO_MANAGER_ID,
    PanelId.CANVAS: EXECUTION_CANVAS_ID,
    PanelId.REGISTRY: MODEL_REGISTRY_ID,
    PanelId.CRITIC: CRITIC_BAR_ID,
}


class HermesApp(App[None]):
    """Four-region dashboard shell: repo-manager | execution-canvas | model-registry + critic-bar."""

    CSS_PATH = "app.tcss"

    #: Operator keybindings (7.9): ctrl+c -> PAUSE (priority), ctrl+q -> confirm quit.
    BINDINGS: ClassVar[list] = list(HERMES_BINDINGS)  # type: ignore[type-arg]  # textual's BindingType is unexported

    def __init__(self, *, workspace: str | None = None) -> None:
        super().__init__()
        #: Workspace root for the repo-manager tree; ``"."`` until 7.11 wires --workspace.
        self.workspace = workspace or "."
        #: PAUSE envelopes emitted by the ctrl+c binding (7.9).
        self._pause_requests: list[Envelope] = []

    def compose(self) -> ComposeResult:
        """Yield the four region placeholders as direct grid children."""
        yield RepoManager(path=self.workspace, id="repo-manager")
        yield ExecutionCanvas(id="execution-canvas")
        yield ModelRegistry(id="model-registry")
        yield CriticBar(id="critic-bar")

    def action_pause(self) -> None:
        """Emit one ``INTERRUPT_REQUEST{PAUSE}`` via the critic bar; never exit (7.9)."""
        envelope = self.query_one(CRITIC_BAR_ID, CriticBar).emit(CriticCommand.PAUSE)
        self._pause_requests.append(envelope)

    def action_request_quit(self) -> None:
        """Push the confirm modal; exit only when the operator confirms (7.9)."""
        self.push_screen(ConfirmQuitScreen(), self._on_quit_confirmed)

    def _on_quit_confirmed(self, confirmed: bool | None) -> None:
        """Exit cleanly on a confirmed Yes; a No (or dismiss) leaves the app running."""
        if confirmed:
            self.exit()

    @property
    def pause_requests(self) -> list[Envelope]:
        """The PAUSE envelopes emitted by the ctrl+c binding, in order (7.9)."""
        return self._pause_requests


def main(argv: list[str] | None = None) -> int:
    """HermesApp CLI entry point (mirrors the broker CLI pattern)."""
    del argv  # no CLI args yet; 7.11 adds --workspace/--self-check
    HermesApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())