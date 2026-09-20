"""IPC CLI: send-all, coverage-check, flood (V11 2.10).

- send-all: send every fixture through the echo server, verify 0 mismatches.
- coverage-check: assert 9/9 EventType members exercised; exit 1 if not.
- flood: push 10k events through the backpressure queue, report drops.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dev_harness.contracts.enums import CriticCommand, EventType
from dev_harness.contracts.events import (
    AgentTokenStreamPayload,
    Envelope,
    InterruptRequestPayload,
)
from dev_harness.ipc.queue import BackpressureQueue

FIXTURES_DEFAULT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "events"


def _socket_path(workspace: str) -> Path:
    from dev_harness.paths import derive_paths

    return derive_paths(workspace).socket_path


def cmd_send_all(workspace: str, fixtures: str) -> int:
    """Send every fixture through the echo server; verify 0 mismatches."""
    from dev_harness.contracts.events import Envelope
    from dev_harness.ipc.client import IpcClient
    from dev_harness.ipc.framing import read_frame

    fixtures_dir = Path(fixtures)
    client = IpcClient(_socket_path(workspace))
    mismatches = 0
    for event_type in EventType:
        fixture = fixtures_dir / f"{event_type.value.lower()}.json"
        env = Envelope.model_validate_json(fixture.read_text(encoding="utf-8"))
        client.send(env)
        reply = read_frame(client._conn)
        if reply != env:
            mismatches += 1
            print(f"MISMATCH {event_type.value}", file=sys.stderr)
    client.close()
    if mismatches:
        print(f"send-all: {mismatches} mismatches", file=sys.stderr)
        return 1
    print("send-all: 9/9 echoed, 0 mismatches")
    return 0


def cmd_coverage_check(workspace: str, fixtures: str) -> int:
    """Assert 9/9 EventType members are exercised by the fixtures."""
    from dev_harness.contracts.events import Envelope

    fixtures_dir = Path(fixtures)
    exercised: set[EventType] = set()
    for event_type in EventType:
        fixture = fixtures_dir / f"{event_type.value.lower()}.json"
        if not fixture.exists():
            print(f"coverage-check: missing fixture {fixture.name}", file=sys.stderr)
            return 1
        env = Envelope.model_validate_json(fixture.read_text(encoding="utf-8"))
        exercised.add(env.type)
    if exercised != set(EventType):
        missing = set(EventType) - exercised
        print(
            f"coverage-check: unexercised: {[m.value for m in missing]}",
            file=sys.stderr,
        )
        return 1
    print("coverage-check: 9/9 EventType members exercised")
    return 0


def cmd_flood(count: int) -> int:
    """Push count events through the backpressure queue; report drops."""
    q = BackpressureQueue()
    control_dropped = 0
    for i in range(count):
        if i % 100 == 0:
            env = Envelope(
                type=EventType.INTERRUPT_REQUEST,
                payload=InterruptRequestPayload(
                    type="INTERRUPT_REQUEST", command=CriticCommand.PAUSE
                ),
            )
            if not q.put(env):
                control_dropped += 1
        token = Envelope(
            type=EventType.AGENT_TOKEN_STREAM,
            payload=AgentTokenStreamPayload(
                type="AGENT_TOKEN_STREAM", seq=i, token="x"
            ),
        )
        q.put(token)
    print(
        f"flood: {count} events, {q.dropped_frames} token drops, {control_dropped} control drops"
    )
    if control_dropped:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dev_harness.ipc.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_send = sub.add_parser("send-all")
    p_send.add_argument("--workspace", required=True)
    p_send.add_argument("--fixtures", default=str(FIXTURES_DEFAULT))
    p_send.set_defaults(func=cmd_send_all)

    p_cov = sub.add_parser("coverage-check")
    p_cov.add_argument("--workspace", required=True)
    p_cov.add_argument("--fixtures", default=str(FIXTURES_DEFAULT))
    p_cov.set_defaults(func=cmd_coverage_check)

    p_flood = sub.add_parser("flood")
    p_flood.add_argument("--count", type=int, default=10000)
    p_flood.set_defaults(func=cmd_flood)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "send-all":
        return int(args.func(args.workspace, args.fixtures))
    if args.command == "coverage-check":
        return int(args.func(args.workspace, args.fixtures))
    if args.command == "flood":
        return int(args.func(args.count))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
