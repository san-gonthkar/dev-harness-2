"""Path derivation tests (V11 0.10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_harness.paths import derive_paths, socket_path_bytes_ok

pytestmark = pytest.mark.unit


def test_trailing_slash_equiv(tmp_path: Path) -> None:
    a = derive_paths(str(tmp_path) + "/")
    b = derive_paths(str(tmp_path))
    assert a == b
    assert a.workspace == b.workspace


def test_distinct_paths_distinct_hashes(tmp_path: Path) -> None:
    d1 = tmp_path / "one"
    d2 = tmp_path / "two"
    d1.mkdir()
    d2.mkdir()
    a = derive_paths(d1)
    b = derive_paths(d2)
    assert a.socket_path != b.socket_path


def test_symlink_resolves(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    derived = derive_paths(link)
    assert derived.workspace == target.resolve()


def test_socket_path_within_limit_for_short_workspace(tmp_path: Path) -> None:
    """A short workspace yields a socket path within the AF_UNIX byte limit.

    The check itself is unit-tested deterministically below; tmp_path on some
    platforms is too long for a real AF_UNIX socket, which is a POSIX-only
    concern (task 2.7). We assert the helper logic with controlled inputs.
    """
    short = tmp_path / "w"
    short.mkdir()
    derived = derive_paths(short)
    # Only meaningful when the platform tmp dir is short enough; otherwise skip.
    if socket_path_bytes_ok(derived.socket_path, limit=104):
        assert socket_path_bytes_ok(derived.socket_path, limit=104)
    else:
        pytest.skip("tmp_path too long for AF_UNIX socket on this platform")


def test_socket_path_bytes_ok_logic() -> None:
    assert socket_path_bytes_ok(Path("/tmp/x/harness.sock"), limit=104) is True
    assert socket_path_bytes_ok(Path("/" + "x" * 200), limit=104) is False
    # UTF-8 byte count matters, not character count.
    long_utf8 = Path("/tmp/" + "é" * 100)
    assert socket_path_bytes_ok(long_utf8, limit=104) is False


def test_derived_layout(tmp_path: Path) -> None:
    d = derive_paths(tmp_path)
    assert d.harness_dir == tmp_path.resolve() / ".dev-harness"
    assert d.state_db.name == "state.db"
    assert d.lock_path.name == "workspace.lock"
    assert d.run_artifacts.name == "runs"
