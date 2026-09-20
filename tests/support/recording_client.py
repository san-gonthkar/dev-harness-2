"""Recording client for the P2 acceptance protocol (V11 2.10).

Connects to the echo server, sends all 9 fixtures, and records the echoed
replies for comparison.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope
from dev_harness.ipc.client import IpcClient
from dev_harness.ipc.framing import read_frame


def send_all(socket_path: Path, fixtures_dir: Path) -> list[Envelope]:
    """Send every fixture and return the echoed replies."""
    client = IpcClient(socket_path)
    replies: list[Envelope] = []
    for event_type in EventType:
        fixture = fixtures_dir / f"{event_type.value.lower()}.json"
        env = Envelope.model_validate_json(fixture.read_text(encoding="utf-8"))
        client.send(env)
        reply = read_frame(client._conn)  # type: ignore[arg-type]
        replies.append(reply)
    client.close()
    return replies


def main() -> int:
    args = sys.argv[1:]
    if "--workspace" not in args or "--fixtures" not in args:
        print(
            "usage: python -m tests.support.recording_client --workspace <dir> --fixtures <dir>",
            file=sys.stderr,
        )
        return 2
    ws = Path(args[args.index("--workspace") + 1])
    fixtures = Path(args[args.index("--fixtures") + 1])
    from dev_harness.paths import derive_paths

    replies = send_all(derive_paths(ws).socket_path, fixtures)
    for reply in replies:
        print(reply.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
