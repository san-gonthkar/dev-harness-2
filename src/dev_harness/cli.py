"""Root ``dev-harness`` entry point (V11 task 7.11).

Usage::

    dev-harness [--workspace PATH] [--self-check]

Without ``--self-check`` the four-panel Hermes TUI launches. With
``--self-check`` the CLI prints the derived socket/DB paths plus broker and
engine health and exits without starting the TUI — the 7.B CLI smoke row.

The workspace must be a git repository; a non-git directory exits 2 with
``NotAGitRepository``. All provider/engine access routes through the broker
client and the engine bootstrap (never a direct adapter call).
"""

from __future__ import annotations

import argparse
import sys

from dev_harness.broker.client import BrokerClient
from dev_harness.contracts.errors import HarnessError, NotAGitRepository
from dev_harness.engine.bootstrap import EngineBootstrap
from dev_harness.paths import derive_paths
from dev_harness.vcs.git import GitAdapter

#: Exit code for a workspace that is not a git repository (7.B row 7.11).
EXIT_NOT_A_REPO = 2
#: Exit code for a self-check that could not reach the broker or engine.
EXIT_SELF_CHECK_FAILED = 1


def _require_git_repo(workspace: str) -> None:
    """Raise NotAGitRepository when ``workspace`` is not a git work tree."""
    if not GitAdapter(workspace).is_repository():
        raise NotAGitRepository(
            f"workspace is not a git repository: {workspace}",
            remediation="Point --workspace at a git repository (run 'git init' first).",
        )


def _self_check(workspace: str) -> int:
    """Print derived paths and broker/engine health; return an exit code."""
    paths = derive_paths(workspace)
    print(f"socket: {paths.socket_path}")
    print(f"db: {paths.state_db}")

    broker = BrokerClient(paths.socket_path)
    try:
        reply = broker.health()
    finally:
        broker.close()
    print(f"broker: {'ok' if reply.ok else 'unavailable'}")

    response = EngineBootstrap(workspace).ensure_daemon()
    print(
        f"engine: thread_id={response.thread_id} "
        f"state={response.state.value} version={response.version}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """``dev-harness`` entry point."""
    parser = argparse.ArgumentParser(prog="dev-harness")
    parser.add_argument("--workspace", default=".", help="Workspace directory")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Print socket/DB paths and broker/engine health, then exit",
    )
    args = parser.parse_args(argv)

    try:
        _require_git_repo(args.workspace)
        if args.self_check:
            return _self_check(args.workspace)
        from dev_harness.tui.app import HermesApp

        HermesApp(workspace=args.workspace).run()
        return 0
    except NotAGitRepository as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_NOT_A_REPO
    except HarnessError as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_SELF_CHECK_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
