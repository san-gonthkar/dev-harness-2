"""JSON Schema export for the V7 HarnessState (V11 0.7).

The committed schema at schemas/harness_state.v7.json is regenerated from the
Pydantic model and must be byte-identical (drift check).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dev_harness.contracts.state import HarnessState

# Anchor at the repo root (parent of the src/ tree) so the committed schema
# lives at <repo>/schemas/harness_state.v7.json regardless of install layout.
_PKG_FILE = Path(__file__).resolve()
_SRC_ROOT = _PKG_FILE.parents[2]  # .../src
_REPO_ROOT = _SRC_ROOT.parent if _SRC_ROOT.name == "src" else _SRC_ROOT
SCHEMA_PATH = _REPO_ROOT / "schemas" / "harness_state.v7.json"


def generate_schema() -> dict:
    """Return the JSON Schema for HarnessState."""
    return HarnessState.model_json_schema()


def write_schema(path: Path = SCHEMA_PATH) -> None:
    """Write the generated schema to disk as pretty JSON."""
    schema = generate_schema()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(schema, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    """CLI: --emit writes the schema; --check verifies it matches the committed file."""
    if "--emit" in sys.argv:
        write_schema()
        print(f"wrote {SCHEMA_PATH}")
        return 0
    if "--check" in sys.argv:
        if not SCHEMA_PATH.exists():
            print(f"missing {SCHEMA_PATH}", file=sys.stderr)
            return 1
        expected = json.dumps(generate_schema(), indent=2) + "\n"
        actual = SCHEMA_PATH.read_text(encoding="utf-8")
        if expected != actual:
            print("schema drift detected", file=sys.stderr)
            return 1
        print("schema matches committed file")
        return 0
    print("usage: python -m dev_harness.contracts.schema --emit | --check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
