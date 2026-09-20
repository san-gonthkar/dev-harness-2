"""Model-swap thrash guard (V11 3.7).

Serializes load-forcing requests so two models are never loaded concurrently.
Exposes model_loaded and emits MODEL_CONFIG_CHANGE on every swap.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope, ModelConfigChangePayload

LoadFn = Callable[[str], Any]


class OllamaLoader:
    """Debounces model loads so a burst never thrashes the loader.

    ``ensure_loaded`` records the newest requested model and returns. A
    background drain thread waits a short settle window, then loads the newest
    pending model. A burst of interleaved requests across two models
    (A B A B A B) therefore results in at most two load events, and loads are
    never concurrent.
    """

    DEBOUNCE_SECONDS = 0.02

    def __init__(
        self,
        load_fn: LoadFn | None = None,
        *,
        emit: Callable[[Envelope], None] | None = None,
    ) -> None:
        self._load_fn = load_fn or (lambda model: None)
        self._emit = emit or (lambda env: None)
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._current_model: str | None = None
        self._pending_model: str | None = None
        self._stop = False
        self.load_events: int = 0
        self.load_windows: list[tuple[float, float]] = []
        self._thread = threading.Thread(target=self._drain_loop, daemon=True)
        self._thread.start()

    @property
    def model_loaded(self) -> str | None:
        """The currently loaded model, or None."""
        with self._lock:
            return self._current_model

    def ensure_loaded(self, model: str) -> None:
        """Request that ``model`` be loaded (debounced)."""
        with self._cv:
            self._pending_model = model
            self._cv.notify_all()

    def flush(self) -> None:
        """Block until the pending model is loaded (test/CLI helper)."""
        with self._cv:
            while self._pending_model != self._current_model:
                self._cv.wait(timeout=0.5)

    def _drain_loop(self) -> None:
        """Wait for the burst to settle, then load the newest pending model."""
        import time

        while not self._stop:
            with self._cv:
                if (
                    self._pending_model is None
                    or self._pending_model == self._current_model
                ):
                    self._cv.wait(timeout=0.1)
                    continue
                # Debounce: wait for the burst to settle.
                self._cv.wait(timeout=self.DEBOUNCE_SECONDS)
                target = self._pending_model
                if target == self._current_model:
                    continue
                start = time.monotonic()
                self._load_fn(target)
                self.load_events += 1
                self._current_model = target
                self.load_windows.append((start, time.monotonic()))
                self._emit(
                    Envelope(
                        type=EventType.MODEL_CONFIG_CHANGE,
                        payload=ModelConfigChangePayload(
                            type="MODEL_CONFIG_CHANGE", provider="ollama", model=target
                        ),
                    )
                )
                self._cv.notify_all()


def _now() -> float:
    import time

    return time.monotonic()
