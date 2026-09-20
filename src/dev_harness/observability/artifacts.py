"""Run artifact store: append-only transcript with size cap + rotation (V11 0.15)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dev_harness.observability.redact import redact


class RunArtifactStore:
    """Append-only per-run transcript of prompts, completions, diffs, test output.

    Entries are ordered; the store rotates at a size cap without losing the
    newest entries, and redacts secrets.
    """

    def __init__(self, path: Path, *, cap_bytes: int = 1_000_000) -> None:
        self.path = path
        self.cap_bytes = cap_bytes
        path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def _write(self, entries: list[dict]) -> None:
        with self.path.open("w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def append(self, entry: dict[str, Any]) -> None:
        """Append one ordered entry, then rotate if over the cap."""
        entries = self._load()
        entry = dict(entry)
        entry["message"] = redact(str(entry.get("message", "")))
        for k, v in list(entry.items()):
            if isinstance(v, str):
                entry[k] = redact(v)
        entries.append(entry)
        # Rotate: drop oldest until under cap, never dropping the newest.
        while len(entries) > 1:
            # Estimate size as JSON bytes.
            size = sum(len(json.dumps(e).encode("utf-8")) for e in entries)
            if size <= self.cap_bytes:
                break
            entries.pop(0)
        self._write(entries)

    def entries(self) -> list[dict]:
        return self._load()
