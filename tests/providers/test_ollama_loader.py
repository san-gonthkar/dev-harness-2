"""Thrash guard tests (V11 3.7)."""

from __future__ import annotations

import itertools
import threading

import pytest

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope
from dev_harness.providers.ollama_loader import OllamaLoader

pytestmark = pytest.mark.unit


def test_6_interleaved_requests_2_models_max_2_loads() -> None:
    """6 interleaved requests across 2 models -> at most 2 load events."""
    loader = OllamaLoader()
    # Interleave: A B A B A B (debounced -> coalesces to the final model).
    for model in ("qwen2.5-coder:7b", "llama3:8b") * 3:
        loader.ensure_loaded(model)
    loader.flush()
    assert loader.load_events <= 2
    assert loader.model_loaded == "llama3:8b"


def test_no_reload_same_model() -> None:
    loader = OllamaLoader()
    loader.ensure_loaded("qwen2.5-coder:7b")
    loader.ensure_loaded("qwen2.5-coder:7b")
    loader.ensure_loaded("qwen2.5-coder:7b")
    loader.flush()
    assert loader.load_events == 1


def test_model_config_change_emitted_per_swap() -> None:
    emitted: list[Envelope] = []
    loader = OllamaLoader(emit=emitted.append)
    loader.ensure_loaded("qwen2.5-coder:7b")
    loader.flush()
    loader.ensure_loaded("llama3:8b")
    loader.flush()
    assert len(emitted) == 2
    assert all(e.type == EventType.MODEL_CONFIG_CHANGE for e in emitted)
    assert emitted[0].payload.model == "qwen2.5-coder:7b"
    assert emitted[1].payload.model == "llama3:8b"


def test_loads_never_concurrent() -> None:
    """Loads are serialized: no overlapping load windows."""
    loader = OllamaLoader()
    for model in ("qwen2.5-coder:7b", "llama3:8b") * 3:
        loader.ensure_loaded(model)
    loader.flush()
    # Sort windows by start; each must end before the next starts.
    windows = sorted(loader.load_windows)
    for (_, end), (next_start, _) in itertools.pairwise(windows):
        assert end <= next_start


def test_concurrent_threads_serialized() -> None:
    """Concurrent ensure_loaded calls from threads never overlap loads."""
    loader = OllamaLoader()
    errors: list[Exception] = []

    def worker(model: str) -> None:
        try:
            for _ in range(10):
                loader.ensure_loaded(model)
        except RuntimeError as exc:  # pragma: no cover - defensive
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("qwen2.5-coder:7b",)),
        threading.Thread(target=worker, args=("llama3:8b",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    loader.flush()
    assert not errors
    assert loader.load_events <= 2
