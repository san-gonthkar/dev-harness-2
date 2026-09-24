"""Sequential integration merge tests (V11 8.9).

Validation matrix (8.B, acceptance is exact) against a REAL temp git repo:

(a) NON-overlapping chunk branches merge cleanly into the primary - every
    chunk's changes are present afterwards;
(b) an OVERLAPPING edit (two chunks touch the same file/region) raises
    ``IntegrationConflict`` naming the conflicting file AND both chunk ids;
(c) on conflict the PRIMARY branch is UNMODIFIED (HEAD + ``git status``
    unchanged; the in-progress merge is aborted).

No ``time.sleep``, no network. ``engine/integrator.py`` is in the 8.C
high-coverage set (95/90) and the mutation focus set (>=80%), so every branch
is exercised.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_harness.contracts.errors import IntegrationConflict, VcsError
from dev_harness.contracts.state import Chunk
from dev_harness.engine.integrator import Integrator


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _porcelain(cwd: Path) -> str:
    return _git(cwd, "status", "--porcelain")


def _chunk(chunk_id: str) -> Chunk:
    return Chunk(chunk_id=chunk_id, title=f"chunk {chunk_id}")


def _branch_with_commit(repo: Path, chunk_id: str, files: dict[str, str]) -> None:
    """Create ``chunk/{chunk_id}`` off the primary and commit ``files``."""
    primary = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "checkout", "-b", f"chunk/{chunk_id}")
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"chunk {chunk_id}")
    _git(repo, "checkout", primary)


# --- acceptance: real git integration ---------------------------------------


@pytest.mark.integration
def test_non_overlapping_branches_merge_clean(tmp_workspace: Path) -> None:
    """(a): non-overlapping chunk branches merge cleanly; all changes present."""
    _branch_with_commit(tmp_workspace, "c1", {"a.txt": "from c1\n"})
    _branch_with_commit(tmp_workspace, "c2", {"b.txt": "from c2\n"})

    merged = Integrator(tmp_workspace).integrate([_chunk("c1"), _chunk("c2")])

    assert merged == ["c1", "c2"]
    assert (tmp_workspace / "a.txt").read_text(encoding="utf-8") == "from c1\n"
    assert (tmp_workspace / "b.txt").read_text(encoding="utf-8") == "from c2\n"
    assert _porcelain(tmp_workspace) == ""


@pytest.mark.integration
def test_fast_forward_when_primary_unchanged(tmp_workspace: Path) -> None:
    """A single chunk branch fast-forwards the primary branch."""
    _branch_with_commit(tmp_workspace, "c1", {"a.txt": "from c1\n"})
    before = _git(tmp_workspace, "rev-parse", "HEAD")

    Integrator(tmp_workspace).integrate([_chunk("c1")])

    after = _git(tmp_workspace, "rev-parse", "HEAD")
    assert after != before
    assert _git(tmp_workspace, "rev-parse", "HEAD") == _git(
        tmp_workspace, "rev-parse", "chunk/c1"
    )


@pytest.mark.negative
def test_overlapping_edit_raises_conflict_naming_file_and_chunks(
    tmp_workspace: Path,
) -> None:
    """(b): overlapping edit -> IntegrationConflict naming file + both chunk ids."""
    (tmp_workspace / "shared.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_workspace, "add", "-A")
    _git(tmp_workspace, "commit", "-m", "add shared")
    _branch_with_commit(tmp_workspace, "c1", {"shared.txt": "c1\n"})
    _branch_with_commit(tmp_workspace, "c2", {"shared.txt": "c2\n"})

    with pytest.raises(IntegrationConflict) as excinfo:
        Integrator(tmp_workspace).integrate([_chunk("c1"), _chunk("c2")])

    message = str(excinfo.value)
    assert "shared.txt" in message
    assert "c1" in message
    assert "c2" in message
    assert excinfo.value.remediation == (
        "Resolve the conflicting file between the named chunk branches."
    )


@pytest.mark.negative
def test_primary_unmodified_on_conflict(tmp_workspace: Path) -> None:
    """(c): on conflict the primary branch HEAD + status are unchanged."""
    (tmp_workspace / "shared.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_workspace, "add", "-A")
    _git(tmp_workspace, "commit", "-m", "add shared")
    _branch_with_commit(tmp_workspace, "c1", {"shared.txt": "c1\n"})
    _branch_with_commit(tmp_workspace, "c2", {"shared.txt": "c2\n"})

    head_before = _git(tmp_workspace, "rev-parse", "HEAD")
    status_before = _porcelain(tmp_workspace)

    with pytest.raises(IntegrationConflict):
        Integrator(tmp_workspace).integrate([_chunk("c1"), _chunk("c2")])

    assert _git(tmp_workspace, "rev-parse", "HEAD") == head_before
    assert _porcelain(tmp_workspace) == status_before
    # The in-progress merge was aborted: no MERGE_HEAD remains.
    assert (tmp_workspace / ".git" / "MERGE_HEAD").exists() is False


@pytest.mark.negative
def test_conflict_after_clean_merge_names_merged_chunk(
    tmp_workspace: Path,
) -> None:
    """The conflict names the already-merged chunk that touched the file."""
    (tmp_workspace / "shared.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_workspace, "add", "-A")
    _git(tmp_workspace, "commit", "-m", "add shared")
    _branch_with_commit(tmp_workspace, "c1", {"shared.txt": "c1\n"})
    _branch_with_commit(tmp_workspace, "c2", {"shared.txt": "c2\n"})

    with pytest.raises(IntegrationConflict) as excinfo:
        Integrator(tmp_workspace).integrate([_chunk("c1"), _chunk("c2")])

    assert "between chunks 'c1' and 'c2'" in str(excinfo.value)


# --- unit: git plumbing -----------------------------------------------------


@pytest.mark.unit
def test_primary_branch_is_current_branch(tmp_workspace: Path) -> None:
    assert Integrator(tmp_workspace).primary_branch == "main"


@pytest.mark.unit
def test_changed_files_raises_for_missing_branch(tmp_workspace: Path) -> None:
    with pytest.raises(VcsError):
        Integrator(tmp_workspace)._changed_files("chunk/does-not-exist")


@pytest.mark.unit
def test_other_chunk_falls_back_to_primary(tmp_workspace: Path) -> None:
    """``_other_chunk`` returns the fallback when no merged chunk touched it."""
    other = Integrator._other_chunk("nope.txt", [("c1", {"a.txt"})], "main")
    assert other == "main"


@pytest.mark.unit
def test_other_chunk_finds_touching_chunk(tmp_workspace: Path) -> None:
    other = Integrator._other_chunk("a.txt", [("c1", {"a.txt"})], "main")
    assert other == "c1"


@pytest.mark.unit
def test_integrate_empty_returns_empty(tmp_workspace: Path) -> None:
    assert Integrator(tmp_workspace).integrate([]) == []
