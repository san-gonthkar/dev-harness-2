"""Corrupt-checkpoint detection, quarantine, rollback (V11 9.4).

Acceptance is exact: a *byte flip* in ``state_json`` fails the ``state_sha256``
digest, the corrupt row is *quarantined* (moved aside, not deleted), and
``get_verified_tuple`` then serves the prior valid checkpoint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_harness.contracts.errors import CorruptCheckpointError, StorageError
from dev_harness.contracts.state import HarnessState
from dev_harness.storage.connection import connect
from dev_harness.storage.integrity import (
    CORRUPTION_REASON,
    QUARANTINE_TABLE,
    get_verified_tuple,
    quarantine_corrupt,
    verify_row,
)
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver

SCOPE = Scope("p1", "t1")


def _state(raw: str) -> HarnessState:
    return HarnessState(
        project_id="p1", workspace_path="/w", thread_id="t1", raw_input=raw
    )


def _flip_first_byte(text: str) -> str:
    """Return ``text`` with its first byte changed (a real byte flip)."""
    return ("x" if text[0] != "x" else "y") + text[1:]


def _corrupt(db_path: Path, checkpoint_id: str) -> str:
    """Flip a byte of a checkpoint's state_json *only* (digest left stale)."""
    conn = connect(db_path)
    raw = conn.execute(
        "SELECT state_json FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE checkpoints SET state_json=? WHERE project_id=? AND thread_id=? AND checkpoint_id=?",
        (_flip_first_byte(str(raw)), "p1", "t1", checkpoint_id),
    )
    conn.commit()
    conn.close()
    return str(raw)


# --- unit: the pure digest check -------------------------------------------


@pytest.mark.unit
def test_verify_row_agrees_with_writer_digest(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    raw = _state("v1").model_dump_json()
    sha = saver._sha256(raw)
    # The independent recompute matches the writer's helper for valid data.
    assert verify_row(raw, sha) is True
    saver.close()


@pytest.mark.unit
def test_verify_row_rejects_byte_flip(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    raw = _state("v1").model_dump_json()
    sha = saver._sha256(raw)
    assert verify_row(_flip_first_byte(raw), sha) is False
    saver.close()


@pytest.mark.unit
def test_get_verified_tuple_missing_returns_none(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    assert get_verified_tuple(saver, SCOPE, "nope") is None
    saver.close()


@pytest.mark.unit
def test_quarantine_corrupt_reports_nothing_when_clean(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    saver.put(SCOPE, _state("v1"), checkpoint_id="c1", created_at=1)
    assert quarantine_corrupt(saver, SCOPE) == []
    saver.close()


# --- integration: real temp SQLite DB --------------------------------------


@pytest.mark.integration
def test_byte_flip_fails_digest_and_serves_prior_valid(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    saver = SqliteSaver(db)
    saver.put(SCOPE, _state("good"), checkpoint_id="c1", created_at=1)
    saver.put(SCOPE, _state("bad"), checkpoint_id="c2", created_at=2)
    _corrupt(db, "c2")

    served = get_verified_tuple(saver, SCOPE, "c2")

    assert served is not None
    # (c) the PRIOR VALID checkpoint is served, byte-for-byte.
    assert served["checkpoint_id"] == "c1"
    assert HarnessState.model_validate_json(served["state_json"]).raw_input == "good"
    saver.close()


@pytest.mark.integration
def test_quarantine_preserves_row_and_removes_from_live_table(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    saver = SqliteSaver(db)
    saver.put(SCOPE, _state("good"), checkpoint_id="c1", created_at=1)
    saver.put(SCOPE, _state("bad"), checkpoint_id="c2", created_at=2)
    _corrupt(db, "c2")

    get_verified_tuple(saver, SCOPE, "c2")

    conn = connect(db)
    # (b) the corrupt row was moved aside, not deleted: it is fully recoverable.
    q = conn.execute(
        f"SELECT state_json, state_sha256, reason FROM {QUARANTINE_TABLE}"
    ).fetchall()
    assert len(q) == 1
    assert q[0]["reason"] == CORRUPTION_REASON
    assert q[0]["state_json"] != "" and q[0]["state_sha256"] != ""
    # It is gone from the checked table, leaving only the prior valid row.
    live = conn.execute("SELECT checkpoint_id FROM checkpoints").fetchall()
    assert [r["checkpoint_id"] for r in live] == ["c1"]
    conn.close()
    saver.close()


@pytest.mark.integration
def test_quarantine_corrupt_moves_every_bad_row(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    saver = SqliteSaver(db)
    saver.put(SCOPE, _state("good"), checkpoint_id="c1", created_at=1)
    saver.put(SCOPE, _state("bad2"), checkpoint_id="c2", created_at=2)
    saver.put(SCOPE, _state("bad3"), checkpoint_id="c3", created_at=3)
    _corrupt(db, "c2")
    _corrupt(db, "c3")

    moved = quarantine_corrupt(saver, SCOPE)

    assert set(moved) == {"c2", "c3"}
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 1
    assert conn.execute(f"SELECT COUNT(*) FROM {QUARANTINE_TABLE}").fetchone()[0] == 2
    conn.close()
    # The surviving row still verifies and reads back.
    served = get_verified_tuple(saver, SCOPE, "c1")
    assert served is not None and served["checkpoint_id"] == "c1"
    saver.close()


# --- negative: the correct failure -----------------------------------------


@pytest.mark.negative
def test_sole_corrupt_checkpoint_raises(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    saver = SqliteSaver(db)
    saver.put(SCOPE, _state("only"), checkpoint_id="c1", created_at=1)
    _corrupt(db, "c1")

    with pytest.raises(CorruptCheckpointError) as exc:
        get_verified_tuple(saver, SCOPE, "c1")

    # Correct error type in the taxonomy, with an actionable remediation.
    assert isinstance(exc.value, StorageError)
    assert "c1" in str(exc.value)
    assert exc.value.remediation
    # The corrupt row was still quarantined before the failure.
    conn = connect(db)
    assert conn.execute(f"SELECT COUNT(*) FROM {QUARANTINE_TABLE}").fetchone()[0] == 1
    conn.close()
    saver.close()


@pytest.mark.negative
def test_quarantined_row_is_gone_so_reread_is_none(tmp_path: Path) -> None:
    # After quarantine the corrupt id no longer exists -> None, not a second
    # failure. Guards against re-quarantining or raising on a moved-aside row.
    db = tmp_path / "db.sqlite"
    saver = SqliteSaver(db)
    saver.put(SCOPE, _state("good"), checkpoint_id="c1", created_at=1)
    saver.put(SCOPE, _state("bad"), checkpoint_id="c2", created_at=2)
    _corrupt(db, "c2")

    assert quarantine_corrupt(saver, SCOPE) == ["c2"]
    assert get_verified_tuple(saver, SCOPE, "c2") is None
    saver.close()


@pytest.mark.negative
def test_tampered_digest_only_also_fails(tmp_path: Path) -> None:
    # The reverse tamper: state_json is intact but state_sha256 is stale. The
    # digest check compares both directions, so this must fail too.
    db = tmp_path / "db.sqlite"
    saver = SqliteSaver(db)
    saver.put(SCOPE, _state("v"), checkpoint_id="c1", created_at=1)
    conn = connect(db)
    conn.execute(
        "UPDATE checkpoints SET state_sha256=? WHERE checkpoint_id=?",
        ("0" * 64, "c1"),
    )
    conn.commit()
    conn.close()

    with pytest.raises(CorruptCheckpointError):
        get_verified_tuple(saver, SCOPE, "c1")
    saver.close()
