"""tmp_workspace: a git-backed workspace factory (V11 0.12)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def make_workspace(
    root: Path,
    *,
    name: str = "workspace",
    files: dict[str, str] | None = None,
    commit: bool = True,
) -> Path:
    """Create a git repo at root/name with optional files and an initial commit.

    Returns the workspace path. ``git rev-parse HEAD`` succeeds afterwards.
    """
    ws = root / name
    ws.mkdir(parents=True, exist_ok=True)
    _git(ws, "init", "-b", "main")
    _git(ws, "config", "user.email", "harness@test.local")
    _git(ws, "config", "user.name", "Harness Test")
    for rel, content in (files or {}).items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    if commit:
        # Ensure at least one tracked file so the initial commit is non-empty.
        if not (files or {}):
            (ws / ".gitkeep").write_text("", encoding="utf-8")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-m", "initial")
    return ws
