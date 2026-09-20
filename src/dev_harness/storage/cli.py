"""Storage CLI: put, get, list, restore subcommands (V11 1.15)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dev_harness.contracts.errors import HarnessError
from dev_harness.contracts.state import HarnessState
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver
from dev_harness.vcs.restore import Restorer


def _db_path(workspace: str) -> Path:
    return Path(workspace) / ".dev-harness" / "state.db"


def cmd_put(workspace: str, file: str, project: str, thread: str) -> int:
    state = HarnessState.model_validate_json(Path(file).read_text(encoding="utf-8"))
    saver = SqliteSaver(_db_path(workspace))
    try:
        cid = saver.put(Scope(project, thread), state)
        print(cid)
        return 0
    finally:
        saver.close()


def cmd_get(workspace: str, project: str, thread: str) -> int:
    saver = SqliteSaver(_db_path(workspace))
    try:
        rows = saver.list(Scope(project, thread), limit=1)
        if not rows:
            print("no checkpoints", file=sys.stderr)
            return 1
        print(rows[0]["state_json"])
        return 0
    finally:
        saver.close()


def cmd_list(workspace: str, project: str, thread: str) -> int:
    saver = SqliteSaver(_db_path(workspace))
    try:
        for row in saver.list(Scope(project, thread), limit=100):
            print(row["checkpoint_id"], row["git_commit_hash"], row["is_paused"])
        return 0
    finally:
        saver.close()


def cmd_restore(workspace: str, to: str) -> int:
    restorer = Restorer(workspace)
    try:
        new_head = restorer.restore(to)
        print(new_head)
        return 0
    except HarnessError as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dev_harness.storage.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_put = sub.add_parser("put")
    p_put.add_argument("--workspace", required=True)
    p_put.add_argument("--file", required=True)
    p_put.add_argument("--project", required=True)
    p_put.add_argument("--thread", required=True)
    p_put.set_defaults(func=cmd_put)

    p_get = sub.add_parser("get")
    p_get.add_argument("--workspace", required=True)
    p_get.add_argument("--project", required=True)
    p_get.add_argument("--thread", required=True)
    p_get.add_argument("--latest", action="store_true")
    p_get.set_defaults(func=cmd_get)

    p_list = sub.add_parser("list")
    p_list.add_argument("--workspace", required=True)
    p_list.add_argument("--project", required=True)
    p_list.add_argument("--thread", required=True)
    p_list.set_defaults(func=cmd_list)

    p_restore = sub.add_parser("restore")
    p_restore.add_argument("--workspace", required=True)
    p_restore.add_argument("--to", required=True)
    p_restore.set_defaults(func=cmd_restore)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "put":
        return cmd_put(args.workspace, args.file, args.project, args.thread)
    if args.command == "get":
        return cmd_get(args.workspace, args.project, args.thread)
    if args.command == "list":
        return cmd_list(args.workspace, args.project, args.thread)
    if args.command == "restore":
        return cmd_restore(args.workspace, args.to)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
