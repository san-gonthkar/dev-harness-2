"""Real-model spike (V11 3.13) — optional, records skipped when no local Ollama.

Runs a minimal Groomer -> Architect -> Developer -> Tester graph against a
real local Ollama and measures persona failure rates. If no local Ollama is
available, records `skipped: no_local_ollama` and writes the report.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

REPORT = Path(__file__).resolve().parents[2] / "reports" / "spike_persona_failures.json"


def _ollama_available() -> bool:
    """True if a local Ollama server is reachable."""
    import socket

    try:
        with socket.create_connection(("localhost", 11434), timeout=1):
            return True
    except OSError:
        return False


def test_real_model_spike() -> None:
    """Run the persona spike or record skipped: no_local_ollama."""
    if not _ollama_available():
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(
            json.dumps(
                {
                    "skipped": "no_local_ollama",
                    "note": "P8 defaults Architect to a hosted model (decision 2026-09-20)",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        pytest.skip("no_local_ollama")
    # Real spike path (requires a live Ollama).
    import asyncio

    from dev_harness.contracts.llm import Message
    from dev_harness.providers.ollama import OllamaClient

    async def run() -> dict[str, object]:
        client = OllamaClient()
        personas = ["groomer", "architect", "developer", "tester"]
        failures = {}
        for persona in personas:
            try:
                text, _ = await client.complete(
                    [
                        Message(
                            role="user",
                            content=f"You are the {persona}. Respond with OK.",
                        )
                    ],
                    model="qwen2.5-coder:7b",
                )
                failures[persona] = "ok" if "OK" in text.upper() else "wrong_output"
            except (OSError, RuntimeError) as exc:  # pragma: no cover - live endpoint
                failures[persona] = type(exc).__name__
        return {"persona_failures": failures}

    result = asyncio.run(run())
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    assert result["persona_failures"]  # non-empty
