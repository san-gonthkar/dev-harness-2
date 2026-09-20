"""JSON Schema export + drift check tests (V11 0.7)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from dev_harness.contracts.schema import SCHEMA_PATH, generate_schema

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "state_v7_golden.json"


def test_schema_is_valid_json_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    instance = json.loads(GOLDEN.read_text(encoding="utf-8"))
    jsonschema.validate(instance, schema)


def test_generated_matches_committed() -> None:
    """Regenerated schema must be byte-identical to the committed file."""
    committed = SCHEMA_PATH.read_text(encoding="utf-8")
    generated = json.dumps(generate_schema(), indent=2) + "\n"
    assert committed == generated


def _resolve_ref(schema: dict, ref: str) -> dict:
    path = ref.lstrip("#/").split("/")
    node: dict = schema
    for part in path:
        node = node[part]
    return node


def test_schema_includes_stopped() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    defs = schema.get("$defs", {})
    execution_state = defs.get("ExecutionState", {})
    assert "STOPPED" in execution_state.get("enum", [])
