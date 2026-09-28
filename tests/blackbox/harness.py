"""Blackbox TUI harness: drive the real app in a real pseudo-terminal.

This is **blackbox**: the application runs as an ordinary process in a real
ConPTY (Windows) or pty (POSIX). It has no idea it is under test — no
in-process construction, no injected driver. That is the difference from
Textual's ``run_test()``, which builds the app in-process with a synthetic
driver (white-box).

The harness:

* spawns ``python -m dev_harness.cli --workspace <ws>`` in a PTY of a chosen
  size;
* reads the raw ANSI byte stream on a background thread;
* feeds it to :class:`pyte.Screen`, so tests assert on **what a user sees**;
* tees the raw bytes to a ``.cast`` file (asciinema format) for recording;
* offers ``wait_for`` / ``send_keys`` / ``send_text`` helpers.

No ``time.sleep`` in tests: ``wait_for`` polls the screen against a deadline.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Self

import pyte

#: Default terminal size for a blackbox run.
DEFAULT_COLS = 100
DEFAULT_ROWS = 30
#: How long to wait for the app to render its first frame.
STARTUP_TIMEOUT = 20.0
#: Poll interval for ``wait_for`` (a poll, not a sleep-and-hope).
POLL_INTERVAL = 0.05


class PtyUnavailableError(RuntimeError):
    """No pseudo-terminal backend is available on this platform."""


def _spawn_pty(cols: int, rows: int, args: list[str]) -> Any:
    """Spawn a PTY of the given size, or raise :class:`PtyUnavailableError`."""
    argv = [sys.executable, "-m", "dev_harness.cli", *args]
    if os.name == "nt":
        try:
            from winpty import PtyProcess  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - platform dependent
            raise PtyUnavailableError(
                "pywinpty is required for blackbox TUI tests on Windows"
            ) from exc
        return PtyProcess.spawn(argv, dimensions=(rows, cols))
    try:
        import ptyprocess  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise PtyUnavailableError(
            "ptyprocess is required for blackbox TUI tests on POSIX"
        ) from exc
    return ptyprocess.PtyProcess.spawn(argv, dimensions=(rows, cols))


class TuiSession:
    """A live TUI running in a PTY, with a screen model and a recording.

    :param workspace: the git workspace to pass to ``--workspace``.
    :param cols: terminal width.
    :param rows: terminal height.
    :param cast_path: where to write the asciinema ``.cast`` recording.
    :param extra_args: additional CLI arguments.
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        cols: int = DEFAULT_COLS,
        rows: int = DEFAULT_ROWS,
        cast_path: Path | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        self.workspace = str(workspace)
        self.cols = cols
        self.rows = rows
        self.cast_path = cast_path
        self.extra_args = extra_args or []
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.Stream(self.screen)
        self._proc: Any = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._raw = bytearray()
        self._events: list[tuple[float, str]] = []
        self._started_at = 0.0
        self.exited = False
        self.exit_code: int | None = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> TuiSession:
        """Spawn the app and begin reading its output."""
        args = ["--workspace", self.workspace, *self.extra_args]
        self._proc = _spawn_pty(self.cols, self.rows, args)
        self._started_at = time.monotonic()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        return self

    def _read_loop(self) -> None:
        """Read PTY chunks, feed the screen model, and record the stream."""
        while not self._stop.is_set():
            try:
                chunk = self._proc.read(4096)
            except EOFError:
                self.exited = True
                break
            except (OSError, ValueError):
                self.exited = True
                break
            if not chunk:
                continue
            with self._lock:
                self._raw.extend(chunk.encode("utf-8", errors="replace"))
                self._events.append((time.monotonic() - self._started_at, chunk))
                self.stream.feed(chunk)

    def stop(self, *, timeout: float = 5.0) -> None:
        """Terminate the app, join the reader, and write the recording."""
        self._stop.set()
        proc = self._proc
        if proc is not None:
            try:
                if proc.isalive():
                    proc.terminate(force=True)
            except Exception:  # noqa: BLE001, S110 - best-effort teardown
                pass
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(timeout)
        self._reader = None
        self._write_cast()

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- recording -----------------------------------------------------------

    def _write_cast(self) -> None:
        """Write the captured stream as an asciinema v2 ``.cast`` file."""
        if self.cast_path is None:
            return
        self.cast_path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "version": 2,
            "width": self.cols,
            "height": self.rows,
            "env": {"TERM": "xterm-256color", "SHELL": "dev-harness"},
        }
        lines = [json.dumps(header)]
        for offset, chunk in self._events:
            lines.append(json.dumps([round(offset, 6), "o", chunk]))
        self.cast_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @property
    def raw_output(self) -> str:
        """The raw ANSI byte stream received so far."""
        with self._lock:
            return bytes(self._raw).decode("utf-8", errors="replace")

    # -- screen access -------------------------------------------------------

    def screen_text(self) -> str:
        """The current screen as plain text (trailing blanks stripped)."""
        with self._lock:
            return "\n".join(line.rstrip() for line in self.screen.display)

    def screen_lines(self) -> list[str]:
        """The current screen rows, right-stripped."""
        with self._lock:
            return [line.rstrip() for line in self.screen.display]

    def contains(self, needle: str) -> bool:
        """True when ``needle`` appears anywhere on the current screen."""
        return needle in self.screen_text()

    def wait_for(
        self,
        predicate: Callable[[TuiSession], bool],
        *,
        timeout: float = STARTUP_TIMEOUT,
        message: str = "",
    ) -> None:
        """Poll ``predicate`` until it holds or ``timeout`` elapses.

        Raises :class:`AssertionError` on timeout, including the current screen
        so a failure is diagnosable without re-running.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(self):
                return
            if self.exited:
                break
            time.sleep(POLL_INTERVAL)
        detail = message or "condition not met"
        raise AssertionError(
            f"{detail} (after {timeout}s)\n--- screen ---\n{self.screen_text()}"
        )

    def wait_for_text(self, needle: str, *, timeout: float = STARTUP_TIMEOUT) -> None:
        """Wait until ``needle`` is visible on screen."""
        self.wait_for(
            lambda s: s.contains(needle),
            timeout=timeout,
            message=f"text not found on screen: {needle!r}",
        )

    # -- input ---------------------------------------------------------------

    def send_keys(self, keys: str) -> None:
        """Send raw key bytes (e.g. ``"\\x03"`` for ctrl+c)."""
        self._proc.write(keys)

    def send_text(self, text: str) -> None:
        """Send literal text."""
        self._proc.write(text)

    def resize(self, cols: int, rows: int) -> None:
        """Resize the PTY and the screen model."""
        self.cols = cols
        self.rows = rows
        self._proc.setwinsize(rows, cols)
        with self._lock:
            self.screen.resize(rows, cols)
