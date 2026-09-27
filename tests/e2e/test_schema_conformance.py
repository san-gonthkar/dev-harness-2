"""Schema-conformance regression over every E2E checkpoint (V11 10.6).

The 10.B row is exact:

* **(a)** 100% of emitted checkpoints **validate** against
  :class:`~dev_harness.contracts.state.HarnessState` - a real E2E run (the 10.1
  harness: the compiled SDLC graph + ``MockLLM``) emits checkpoints, and every
  one is ``model_validate``-d and validated against the committed JSON schema.
* **(b)** an **added field fails** until the schema + fixture are updated
  together - ``HarnessState`` is ``extra="forbid"`` and the committed schema is
  ``additionalProperties: false``, so a checkpoint carrying an unknown field is
  **rejected** by both.

The committed schema at ``schemas/harness_state.v7.json`` is asserted to be in
sync with the model (drift detection); if it ever drifts, this test fails rather
than silently rewriting the schema.

No network (``MockLLM`` + sockets disabled by the reused harness), no
``time.sleep``, no ``tui/`` import, no new dependencies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import jsonschema
import pytest
from pydantic import ValidationError

from dev_harness.contracts.schema import SCHEMA_PATH, generate_schema
from dev_harness.contracts.state import Chunk, HarnessState
from dev_harness.storage.checkpoint_binding import CheckpointBinding
from dev_harness.storage.sqlite_saver import SqliteSaver
from tests.e2e.test_full_sdlc import (
    _BASE_TEST,
    _GREEN_TEST,
    _MockPersonaClient,
    _pause_then_resume,
)
from tests.support.mock_llm import MockLLM
from tests.support.workspace import make_workspace

#: A minimal valid instance: only the three required fields.
_MINIMAL: dict[str, object] = {
    "project_id": "p",
    "workspace_path": "/ws",
    "thread_id": "t",
}


def _committed_schema() -> dict[str, object]:
    """Load the committed JSON schema from disk."""
    return cast(
        "dict[str, object]", json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    )


@pytest.mark.integration
async def test_every_emitted_checkpoint_validates(tmp_path: Path) -> None:
    """(a) every checkpoint a real E2E emits validates against HarnessState."""
    repo = make_workspace(tmp_path, files={"tests/test_base.py": _BASE_TEST})
    chunk = Chunk(chunk_id="c1", title="Implement addition")
    client = _MockPersonaClient(
        MockLLM(seed="schema"), developer_writes={"tests/test_chunk_c1.py": _GREEN_TEST}
    )
    test_command = (sys.executable, "-c", "print('1 passed in 0.01s')")

    scope, checkpoint_id, sealed, _halted, final = await _pause_then_resume(
        repo, client, chunk, test_command=test_command, thread_id="schema-conf"
    )

    saver = SqliteSaver(CheckpointBinding(repo).db_path)
    try:
        rows = saver.list(scope)
    finally:
        saver.close()

    assert rows, "the E2E emitted no checkpoints"
    assert any(row["checkpoint_id"] == checkpoint_id for row in rows), (
        "the pause checkpoint was not among the emitted checkpoints"
    )

    schema = _committed_schema()
    for row in rows:
        payload: dict[str, object] = json.loads(str(row["state_json"]))
        # (a) validates against the Pydantic model ...
        state = HarnessState.model_validate(payload)
        # ... and against the committed JSON schema.
        jsonschema.validate(payload, schema)
        # The dump round-trips field-for-field (no lossy serialization).
        assert HarnessState.model_validate(state.model_dump(mode="json")) == state

    # The sealed and final states are emitted states too; both validate.
    assert HarnessState.model_validate(sealed.model_dump(mode="json")) == sealed
    assert HarnessState.model_validate(final.model_dump(mode="json")) == final

    # The committed schema is in sync with the model (no silent drift).
    assert schema == generate_schema(), "schemas/harness_state.v7.json has drifted"


@pytest.mark.negative
def test_added_field_is_rejected_until_schema_and_fixture_updated() -> None:
    """(b) an added field is rejected by both the model and the schema."""
    # The baseline instance is accepted.
    HarnessState.model_validate(_MINIMAL)

    # An added field is rejected: HarnessState is extra="forbid".
    with pytest.raises(ValidationError):
        HarnessState.model_validate({**_MINIMAL, "unexpected_field": 1})

    # The committed schema agrees: additionalProperties is false, so the same
    # instance is rejected there too. A field can only be added by updating the
    # model, the schema, and the fixture together.
    schema = _committed_schema()
    assert schema["additionalProperties"] is False
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**_MINIMAL, "unexpected_field": 1}, schema)
