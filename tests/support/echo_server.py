"""Echo server for the P2 acceptance protocol (V11 2.10).

Echoes every received envelope back to the sender. Used by the IPC CLI
send-all subcommand and the acceptance protocol.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dev_harness.contracts.events import Envelope
from dev_harness.ipc.server import IpcServer


def echo_handler(envelope: Envelope) -> Envelope:
    """Return the envelope unchanged (echo)."""
    return envelope


def main() -> int:
    args = sys.argv[1:]
    if "--workspace" not in args:
        print(
            "usage: python -m dev_harness.ipc.server --workspace <dir> [--echo]",
            file=sys.stderr,
        )
        return 2
    ws = Path(args[args.index("--workspace") + 1])
    from dev_harness.paths import derive_paths

    socket_path = derive_paths(ws).socket_path
    server = IpcServer(socket_path, handler=echo_handler)
    server.start()
    print(f"echo server listening on {socket_path}", flush=True)
    try:
        import time

        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
