"""Crash-recovery tests (V11 9.1).

Validation matrix (9.B, acceptance is exact):

(a) an UN-FINALIZED session is DETECTED - a session with a checkpoint but no
    clean-shutdown marker;
(b) resume-from-checkpoint is OFFERED - ``detect_unfinalized_session`` returns a
    :class:`RecoveryPlan` (``None`` when the session ended cleanly);
(c) each worker worktree is RESTORED to its recorded HEAD + diff (8.21b);
(d) state equals the last seal FIELD-FOR-FIELD.

The 9.B row is NIGHTLY (``-m e2e``, SIGKILL). SIGKILL is POSIX-only, so the
``e2e`` test is deselected in the smoke lane and a PR-tier test simulates the
crash by leaving an un-finalized checkpoint (no SIGKILL needed). Real temp git
repo; no ``time.sleep``, no network, no ``tui/`` import.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from dev_harness.contracts.errors import CorruptCheckpointError, RecoveryError
from dev_harness.contracts.state import Chunk, HarnessState
from dev_harness.engine.worker_workspace import WorkerWorkspace
from dev_harness.recovery.session_recovery import (
    SEAL_CHECKPOINT_ID,
    RecoveryPlan,
    detect_unfinalized_session,
    resume,
)
from dev_harness.storage.checkpoint_binding import CheckpointBinding
from dev_harness.storage.connection import connect
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout


def _state(workspace: Path, thread_id: str = "t1") -> HarnessState:
    return HarnessState(
        project_id="p1", workspace_path=str(workspace), thread_id=thread_id
    )


def _put(
    workspace: Path,
    *,
    checkpoint_id: str,
    worktree_state: dict[str, dict[str, str]] | None = None,
    is_paused: bool = False,
) -> HarnessState:
    state = _state(workspace)
    CheckpointBinding(workspace).put_bound(
        Scope("p1", "t1"),
        state,
        checkpoint_id=checkpoint_id,
        is_paused=is_paused,
        worktree_state=worktree_state,
    )
    return state


def _root(workspace: Path, worker_id: str) -> Path:
    """The worktree root for ``worker_id`` (created by ``resume``)."""
    return workspace / ".dev-harness" / "worktrees" / worker_id


def _corrupt(workspace: Path, checkpoint_id: str) -> None:
    """Flip the first byte of a checkpoint's ``state_json`` (digest mismatch)."""
    conn = connect(workspace / ".dev-harness" / "state.db")
    try:
        row = conn.execute(
            "SELECT state_json FROM checkpoints WHERE checkpoint_id=?",
            (checkpoint_id,),
        ).fetchone()
        assert row is not None
        original = str(row["state_json"])
        flipped = ("X" if original[0] != "X" else "Y") + original[1:]
        conn.execute(
            "UPDATE checkpoints SET state_json=? WHERE checkpoint_id=?",
            (flipped, checkpoint_id),
        )
        conn.commit()
    finally:
        conn.close()


# --- unit: detection --------------------------------------------------------


@pytest.mark.unit
def test_detect_none_when_no_checkpoints(tmp_workspace: Path) -> None:
    assert detect_unfinalized_session(tmp_workspace) is None


@pytest.mark.unit
def test_detect_none_when_newest_is_seal(tmp_workspace: Path) -> None:
    _put(tmp_workspace, checkpoint_id=SEAL_CHECKPOINT_ID, is_paused=True)
    assert detect_unfinalized_session(tmp_workspace) is None


@pytest.mark.unit
def test_detect_offers_resume_for_unfinalized(tmp_workspace: Path) -> None:
    state = _put(tmp_workspace, checkpoint_id="cp1")
    plan = detect_unfinalized_session(tmp_workspace)
    assert plan is not None
    assert plan.checkpoint_id == "cp1"
    assert plan.scope == Scope("p1", "t1")
    assert plan.state.model_dump(mode="json") == state.model_dump(mode="json")


@pytest.mark.unit
def test_detect_none_when_db_has_no_schema(tmp_workspace: Path) -> None:
    db = tmp_workspace / ".dev-harness" / "state.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"")
    assert detect_unfinalized_session(tmp_workspace) is None


@pytest.mark.unit
def test_detect_none_when_table_is_empty(tmp_workspace: Path) -> None:
    saver = SqliteSaver(tmp_workspace / ".dev-harness" / "state.db")
    saver.close()
    assert detect_unfinalized_session(tmp_workspace) is None


@pytest.mark.unit
def test_detect_none_when_verified_read_returns_none(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put(tmp_workspace, checkpoint_id="cp1")
    monkeypatch.setattr(
        "dev_harness.recovery.session_recovery.get_verified_tuple",
        lambda *a, **k: None,
    )
    assert detect_unfinalized_session(tmp_workspace) is None


@pytest.mark.unit
def test_detect_none_when_corrupt_newest_falls_back_to_seal(
    tmp_workspace: Path,
) -> None:
    _put(tmp_workspace, checkpoint_id=SEAL_CHECKPOINT_ID, is_paused=True)
    # "zz-corrupt" sorts after "shutdown" (created_at ties -> id DESC), so it is
    # the newest row; corrupting it forces the verified read to fall back to the
    # seal, which means the session is finalized.
    _put(tmp_workspace, checkpoint_id="zz-corrupt")
    _corrupt(tmp_workspace, "zz-corrupt")
    assert detect_unfinalized_session(tmp_workspace) is None


# --- integration: resume restores worktrees + state -------------------------


@pytest.mark.integration
def test_resume_restores_worktree_and_state_field_for_field(
    tmp_workspace: Path,
) -> None:
    ws = WorkerWorkspace(tmp_workspace)
    root = ws.bind("worker-1", Chunk(chunk_id="c1", title="c1"))
    (root / "tracked.txt").write_text("v1\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-m", "v1")
    (root / "tracked.txt").write_text("v2-uncommitted\n", encoding="utf-8")
    (root / "new.txt").write_text("untracked work\n", encoding="utf-8")
    snapshot = ws.capture("worker-1")

    state = _put(
        tmp_workspace,
        checkpoint_id="cp1",
        worktree_state=ws.capture_map(["worker-1"]),
    )

    # Simulate the crash: the worktree loses its uncommitted work.
    _git(root, "reset", "--hard")
    _git(root, "clean", "-fd")
    assert not (root / "new.txt").exists()

    # (a)/(b) the un-finalized session is detected and resume is offered.
    plan = detect_unfinalized_session(tmp_workspace)
    assert plan is not None
    assert plan.worktrees == {"worker-1": snapshot}

    # (c) resume restores the worktree to its recorded HEAD + diff.
    resumed = resume(plan, tmp_workspace)
    restored_root = _root(tmp_workspace, "worker-1")
    assert _git(restored_root, "rev-parse", "HEAD").strip() == snapshot["head"]
    assert (restored_root / "tracked.txt").read_text(encoding="utf-8") == (
        "v2-uncommitted\n"
    )
    assert (restored_root / "new.txt").read_text(encoding="utf-8") == (
        "untracked work\n"
    )

    # (d) state equals the last seal field-for-field.
    saver = SqliteSaver(tmp_workspace / ".dev-harness" / "state.db")
    row = saver.get_tuple(Scope("p1", "t1"), "cp1")
    saver.close()
    assert row is not None
    sealed = HarnessState.model_validate_json(str(row["state_json"]))
    assert resumed.model_dump(mode="json") == sealed.model_dump(mode="json")
    assert resumed.model_dump(mode="json") == state.model_dump(mode="json")


@pytest.mark.integration
def test_resume_reclaims_stale_worktree_then_rebinds(tmp_workspace: Path) -> None:
    ws = WorkerWorkspace(tmp_workspace)
    root = ws.bind("worker-1", Chunk(chunk_id="c1", title="c1"))
    (root / "out.txt").write_text("in-flight\n", encoding="utf-8")
    _put(
        tmp_workspace,
        checkpoint_id="cp1",
        worktree_state=ws.capture_map(["worker-1"]),
    )
    # The crashed process left the worktree directory behind (no .pid sidecar).
    assert root.exists()

    plan = detect_unfinalized_session(tmp_workspace)
    assert plan is not None
    resume(plan, tmp_workspace)

    # The stale worktree was reclaimed and recreated with the captured work.
    assert root.exists()
    assert (root / "out.txt").read_text(encoding="utf-8") == "in-flight\n"


@pytest.mark.integration
def test_detect_quarantines_corrupt_newest_and_serves_prior(
    tmp_workspace: Path,
) -> None:
    _put(tmp_workspace, checkpoint_id="cp1")
    _put(tmp_workspace, checkpoint_id="cp2")
    _corrupt(tmp_workspace, "cp2")

    plan = detect_unfinalized_session(tmp_workspace)
    assert plan is not None
    assert plan.checkpoint_id == "cp1"


# --- negative ---------------------------------------------------------------


@pytest.mark.negative
def test_detect_raises_when_only_checkpoint_is_corrupt(tmp_workspace: Path) -> None:
    _put(tmp_workspace, checkpoint_id="cp1")
    _corrupt(tmp_workspace, "cp1")
    with pytest.raises(CorruptCheckpointError):
        detect_unfinalized_session(tmp_workspace)


@pytest.mark.negative
def test_resume_wraps_restore_failure_in_recovery_error(tmp_workspace: Path) -> None:
    class _FailingWorkspace:
        def bind(self, worker_id: str, chunk: Chunk) -> Path:
            raise RuntimeError("worktree creation exploded")

    plan = RecoveryPlan(
        scope=Scope("p1", "t1"),
        checkpoint_id="cp1",
        state=_state(tmp_workspace),
        worktrees={"worker-1": {"head": "abc", "diff": ""}},
    )
    with pytest.raises(RecoveryError) as excinfo:
        resume(plan, tmp_workspace, worker_workspace=_FailingWorkspace())  # type: ignore[arg-type]
    assert "worker-1" in str(excinfo.value)
    assert excinfo.value.remediation


@pytest.mark.negative
def test_resume_reraises_recovery_error_from_restore(tmp_workspace: Path) -> None:
    class _RecoveryFailingWorkspace:
        def bind(self, worker_id: str, chunk: Chunk) -> Path:
            raise RecoveryError("already a recovery failure")

    plan = RecoveryPlan(
        scope=Scope("p1", "t1"),
        checkpoint_id="cp1",
        state=_state(tmp_workspace),
        worktrees={"worker-1": {"head": "abc", "diff": ""}},
    )
    with pytest.raises(RecoveryError, match="already a recovery failure"):
        resume(plan, tmp_workspace, worker_workspace=_RecoveryFailingWorkspace())  # type: ignore[arg-type]


# --- e2e: SIGKILL mid-run then restart (NIGHTLY, POSIX-only) ----------------

_CHILD_SCRIPT = """
import os, signal, sys
from pathlib import Path
from dev_harness.contracts.state import Chunk, HarnessState
from dev_harness.engine.worker_workspace import WorkerWorkspace
from dev_harness.storage.checkpoint_binding import CheckpointBinding
from dev_harness.storage.sqlite_saver import Scope

ws = Path(sys.argv[1])
w = WorkerWorkspace(ws)
root = w.bind("worker-1", Chunk(chunk_id="c1", title="c1"))
(root / "tracked.txt").write_text("v1\\n", encoding="utf-8")
(root / "new.txt").write_text("untracked work\\n", encoding="utf-8")
state = HarnessState(project_id="p1", workspace_path=str(ws), thread_id="t1")
CheckpointBinding(ws).put_bound(
    Scope("p1", "t1"), state, worktree_state=w.capture_map(["worker-1"])
)
os.kill(os.getpid(), signal.SIGKILL)
"""


@pytest.mark.e2e
@pytest.mark.skipif(os.name != "posix", reason="SIGKILL is POSIX-only")
def test_sigkill_mid_run_then_restart_resumes(tmp_workspace: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_SCRIPT, str(tmp_workspace)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == -signal.SIGKILL

    plan = detect_unfinalized_session(tmp_workspace)
    assert plan is not None
    assert plan.worktrees["worker-1"]["head"] != ""

    resumed = resume(plan, tmp_workspace)
    root = _root(tmp_workspace, "worker-1")
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "v1\n"
    assert (root / "new.txt").read_text(encoding="utf-8") == "untracked work\n"
    assert resumed.model_dump(mode="json") == plan.state.model_dump(mode="json")
