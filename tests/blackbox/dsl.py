"""Cypress/Selenium-style fluent API for blackbox TUI testing.

The application runs as a **real process in a real PTY** — this module only
gives it a declarative, chainable API so a test reads like a user story::

    tui = launch(workspace)
    tui.should_see("field").should_see("value")
    tui.press("ctrl+c").should_still_be_running()
    tui.press("ctrl+q").should_see("Quit").type("y").should_have_exited()

Design mirrors Cypress:

* **retry-until-timeout** — ``should_see`` polls the rendered screen instead of
  asserting once, so a slow render is not a flake;
* **chainable** — every action returns the session;
* **user-level selectors** — you assert on *visible text*, never on widget IDs
  or internal state;
* **auto-recording** — the raw PTY stream is written as an asciinema ``.cast``.

Key names are Cypress-style (``ctrl+c``, ``enter``, ``tab``) and mapped to the
bytes a terminal actually sends.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

from tests.blackbox.harness import DEFAULT_COLS, DEFAULT_ROWS, TuiSession

#: Cypress-style key names -> the bytes a terminal sends.
KEYS: dict[str, str] = {
    "enter": "\r",
    "return": "\r",
    "tab": "\t",
    "escape": "\x1b",
    "esc": "\x1b",
    "space": " ",
    "backspace": "\x7f",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "ctrl+a": "\x01",
    "ctrl+c": "\x03",
    "ctrl+d": "\x04",
    "ctrl+q": "\x11",
    "ctrl+s": "\x13",
    "ctrl+z": "\x1a",
    "ctrl+\\": "\x1c",
}


def launch(
    workspace: str | Path,
    *,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
    cast_path: Path | None = None,
    args: list[str] | None = None,
) -> Tui:
    """Launch the real TUI in a PTY and return the chainable session.

    This is the ``cy.visit()`` of the harness: it starts the actual
    application process, not an in-process copy.
    """
    session = TuiSession(
        workspace, cols=cols, rows=rows, cast_path=cast_path, extra_args=args
    )
    session.start()
    return Tui(session)


def key_bytes(name: str) -> str:
    """Map a Cypress-style key name to its terminal bytes.

    A single character passes through unchanged, so ``key_bytes("y") == "y"``.
    """
    lowered = name.lower()
    if lowered in KEYS:
        return KEYS[lowered]
    if len(name) == 1:
        return name
    raise ValueError(f"unknown key: {name!r}")


class Tui:
    """A chainable wrapper over :class:`TuiSession` (Cypress-style).

    Every method returns ``self`` so calls chain. Assertions retry until the
    timeout, so a slow render is not a failure.
    """

    def __init__(self, session: TuiSession) -> None:
        self.session = session

    # -- assertions (retry until timeout) ------------------------------------

    def should_see(self, text: str, *, timeout: float = 20.0) -> Tui:
        """Assert ``text`` becomes visible on screen (retries)."""
        self.session.wait_for_text(text, timeout=timeout)
        return self

    def should_not_see(self, text: str, *, timeout: float = 5.0) -> Tui:
        """Assert ``text`` never appears within ``timeout``."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.session.contains(text):
                raise AssertionError(
                    f"unexpected text on screen: {text!r}\n"
                    f"--- screen ---\n{self.session.screen_text()}"
                )
            time.sleep(0.05)
        return self

    def should_still_be_running(self, *, timeout: float = 2.0) -> Tui:
        """Assert the app has not exited."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.session.exited:
                raise AssertionError("app exited unexpectedly")
            time.sleep(0.05)
        return self

    def should_have_exited(self, *, timeout: float = 10.0) -> Tui:
        """Assert the app exits within ``timeout``."""
        self.session.wait_for(
            lambda s: s.exited, timeout=timeout, message="app did not exit"
        )
        return self

    # -- actions -------------------------------------------------------------

    def press(self, *keys: str) -> Tui:
        """Press one or more Cypress-style keys (e.g. ``press("ctrl+c")``)."""
        for key in keys:
            self.session.send_keys(key_bytes(key))
        return self

    def type(self, text: str) -> Tui:
        """Type literal text."""
        self.session.send_text(text)
        return self

    def resize(self, cols: int, rows: int) -> Tui:
        """Resize the terminal."""
        self.session.resize(cols, rows)
        return self

    def wait(self, seconds: float) -> Tui:
        """Wait a fixed time — prefer ``should_see``; use sparingly."""
        import time

        time.sleep(seconds)
        return self

    # -- inspection ----------------------------------------------------------

    def screen(self) -> str:
        """The current rendered screen as text."""
        return self.session.screen_text()

    def lines(self) -> list[str]:
        """The current rendered screen rows."""
        return self.session.screen_lines()

    def contains(self, text: str) -> bool:
        """True when ``text`` is currently visible."""
        return self.session.contains(text)

    def screenshot(self, path: Path) -> Path:
        """Write the current screen to a text file (a poor man's screenshot)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.screen(), encoding="utf-8")
        return path

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Stop the app and flush the recording."""
        self.session.stop()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
