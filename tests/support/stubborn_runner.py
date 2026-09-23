"""StubbornRunner: hostile interrupt workload (V11 6.9, 6.D step 3).

A test binary that spawns ``--children`` (default 3) grandchild processes,
traps ``SIGINT`` with a handler that does *not* exit, and writes a counter
line to ``--output`` continuously. It exists so the escalating interrupt
(6.5) can be demonstrated against a workload a plain ``SIGINT`` cannot kill:
only ``SIGKILL`` over the whole process group ends it.

Every loop is bounded. The runner stops after ``--max-iterations`` (0 =
unbounded) or as soon as ``--stop-file`` exists, so it can never run forever
inside a test. The grandchildren inherit the runner's process group, so a
single ``killpg`` reaches the whole tree.

POSIX: spawn with ``start_new_session=True`` (or pass ``--new-session``) so
the runner leads its own group and ``killpg`` can target it. Windows has no
``killpg``; the runner still runs and traps SIGINT, and the ``.ps1`` platform
stub covers the live kill path.

Run standalone::

    python tests/support/stubborn_runner.py --output /tmp/runner.out
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence

DEFAULT_CHILDREN = 3
DEFAULT_POLL_INTERVAL = 0.05

# Grandchild program: ignore SIGINT, then write a counter line to its own file
# until the stop file appears or the iteration limit is reached. Bounded so it
# cannot spin forever if the parent is killed without a stop file.
_CHILD_SOURCE = (
    "import os, signal, sys, time\n"
    "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
    "path, stop, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])\n"
    "i = 0\n"
    "while not (stop and os.path.exists(stop)) and (limit <= 0 or i < limit):\n"
    "    with open(path, 'a') as fh:\n"
    "        fh.write(f'{i}\\n')\n"
    "    i += 1\n"
    "    time.sleep(0.05)\n"
)


def _non_negative_int(value: str) -> int:
    """argparse type: an integer >= 0."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"must be non-negative: {value!r}")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the runner's command line (pure; spawns nothing)."""
    parser = argparse.ArgumentParser(prog="stubborn_runner")
    parser.add_argument("--output", required=True, help="file to append to")
    parser.add_argument(
        "--children",
        type=_non_negative_int,
        default=DEFAULT_CHILDREN,
        help="number of grandchild processes to spawn",
    )
    parser.add_argument(
        "--max-iterations",
        type=_non_negative_int,
        default=0,
        help="stop after N writes (0 = unbounded, bounded by --stop-file)",
    )
    parser.add_argument(
        "--stop-file",
        default=None,
        help="stop once this path exists (default: <output>.stop)",
    )
    parser.add_argument(
        "--new-session",
        action="store_true",
        help="call os.setsid() so the runner leads its own process group (POSIX)",
    )
    return parser.parse_args(argv)


def child_argv(output: str, stop_file: str | None, max_iterations: int) -> list[str]:
    """The argv for one grandchild writing to ``output`` (pure)."""
    return [
        sys.executable,
        "-c",
        _CHILD_SOURCE,
        output,
        stop_file or "",
        str(max_iterations),
    ]


def should_stop(iteration: int, max_iterations: int, stop_file: str | None) -> bool:
    """True when the write loop must end (bounded; never runs forever)."""
    if max_iterations > 0 and iteration >= max_iterations:
        return True
    return bool(stop_file) and os.path.exists(stop_file)


def spawn_children(
    count: int,
    output: str,
    *,
    stop_file: str | None,
    max_iterations: int,
) -> list[subprocess.Popen[bytes]]:
    """Spawn ``count`` grandchildren, each writing to ``<output>.child<i>``."""
    procs: list[subprocess.Popen[bytes]] = []
    for index in range(count):
        argv = child_argv(f"{output}.child{index}", stop_file, max_iterations)
        procs.append(subprocess.Popen(argv))
    return procs


def wait_children_ready(
    output: str,
    count: int,
    *,
    timeout: float = 5.0,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> bool:
    """Wait (bounded) until every grandchild has written at least once."""
    if count == 0:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(
            os.path.exists(f"{output}.child{i}")
            and os.path.getsize(f"{output}.child{i}") > 0
            for i in range(count)
        ):
            return True
        time.sleep(poll_interval)
    return False


def run(
    output: str,
    *,
    max_iterations: int,
    stop_file: str | None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> int:
    """Append counter lines to ``output`` until a bound is hit; return the count.

    Each line is also echoed to stdout (flushed) so a test can observe growth
    event-driven, without polling the file with a sleep.
    """
    iteration = 0
    while not should_stop(iteration, max_iterations, stop_file):
        line = f"{iteration} {time.time()}"
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        print(line, flush=True)
        iteration += 1
        time.sleep(poll_interval)
    return iteration


def _ignore_sigint(_signum: int, _frame: object) -> None:
    """SIGINT handler that deliberately does not exit (only SIGKILL can)."""


def main(argv: Sequence[str] | None = None) -> int:
    """Spawn the grandchildren, write continuously, and clean up on exit."""
    args = parse_args(argv)
    if args.new_session and hasattr(os, "setsid"):
        os.setsid()
    signal.signal(signal.SIGINT, _ignore_sigint)
    stop_file = args.stop_file or f"{args.output}.stop"
    children = spawn_children(
        args.children,
        args.output,
        stop_file=stop_file,
        max_iterations=args.max_iterations,
    )
    try:
        if not wait_children_ready(args.output, args.children):
            print("children-not-ready", flush=True)
        print("children-ready", flush=True)
        run(
            args.output,
            max_iterations=args.max_iterations,
            stop_file=stop_file,
        )
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
