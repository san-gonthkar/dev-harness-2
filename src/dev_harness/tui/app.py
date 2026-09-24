"""HermesApp — the four-panel Hermes TUI shell (V11 task 7.1).

The shell owns the CSS grid and the four region placeholders. The real
panels mount their widgets into these exact IDs in tasks 7.2/7.3/7.5/7.6;
the bridge (7.7), throttle (7.8) and keybindings (7.9) attach later.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.widgets import Static

from dev_harness.contracts.enums import PanelId
from dev_harness.tui.panels.execution_canvas import ExecutionCanvas

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

    #: Keybindings arrive in 7.9; the shell deliberately binds nothing yet.
    BINDINGS: ClassVar[list] = []  # type: ignore[type-arg]  # textual's BindingType is unexported

    def compose(self) -> ComposeResult:
        """Yield the four region placeholders as direct grid children."""
        yield Static("repo-manager", id="repo-manager")
        yield ExecutionCanvas(id="execution-canvas")
        yield Static("model-registry", id="model-registry")
        yield Static("critic-bar", id="critic-bar")


def main(argv: list[str] | None = None) -> int:
    """HermesApp CLI entry point (mirrors the broker CLI pattern)."""
    del argv  # no CLI args yet; 7.11 adds --workspace/--self-check
    HermesApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())