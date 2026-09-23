"""Critic transition table as executable spec (V11 0.22).

ExecutionState x CriticCommand -> result. IllegalTransitionError carries the
offending (state, command) pair. This table is the single source of truth for
6.1, 6.2, and 6.D step 1.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Literal

from dev_harness.contracts.enums import CriticCommand, ExecutionState
from dev_harness.contracts.errors import IllegalTransitionError

# Result: either a target state, or ILLEGAL.
Result = ExecutionState | Literal["ILLEGAL"]


# already flag semantics:
#   already:false on a no-op means the command was accepted but required no transition
#   already:true on an idempotent repeat means the requested state is already in effect
@dataclass(frozen=True)
class TransitionResult:
    result: Result
    already: bool = False


# 16-cell table (V11 0.22, confirmed decisions 2026-09-20).
_TRANSITIONS: dict[ExecutionState, dict[CriticCommand, TransitionResult]] = {
    ExecutionState.READY: {
        CriticCommand.START: TransitionResult(ExecutionState.RUNNING),
        CriticCommand.PAUSE: TransitionResult(ExecutionState.READY, already=False),
        CriticCommand.RESUME: TransitionResult("ILLEGAL"),
        CriticCommand.STOP: TransitionResult(ExecutionState.STOPPED),
    },
    ExecutionState.RUNNING: {
        CriticCommand.START: TransitionResult("ILLEGAL"),
        CriticCommand.PAUSE: TransitionResult(ExecutionState.PAUSED),
        CriticCommand.RESUME: TransitionResult("ILLEGAL"),
        CriticCommand.STOP: TransitionResult(ExecutionState.STOPPED),
    },
    ExecutionState.PAUSED: {
        CriticCommand.START: TransitionResult("ILLEGAL"),
        CriticCommand.PAUSE: TransitionResult(ExecutionState.PAUSED, already=True),
        CriticCommand.RESUME: TransitionResult(ExecutionState.RUNNING),
        CriticCommand.STOP: TransitionResult(ExecutionState.STOPPED),
    },
    ExecutionState.STOPPED: {
        CriticCommand.START: TransitionResult("ILLEGAL"),
        CriticCommand.PAUSE: TransitionResult("ILLEGAL"),
        CriticCommand.RESUME: TransitionResult("ILLEGAL"),
        CriticCommand.STOP: TransitionResult(ExecutionState.STOPPED, already=True),
    },
}


def apply_transition(state: ExecutionState, command: CriticCommand) -> TransitionResult:
    """Apply a (state, command) pair per the 0.22 table.

    Raises IllegalTransitionError on an ILLEGAL cell carrying the pair.
    """
    result = _TRANSITIONS[state][command]
    if result.result == "ILLEGAL":
        raise IllegalTransitionError(
            f"illegal transition {state.value} + {command.value}",
            state=state,
            command=command,
            remediation="Send a legal command for the current state per the 0.22 table.",
        )
    return result


def transition_table() -> dict[str, dict[str, str]]:
    """Return the table in a printable form for the CLI."""
    return {
        state.value: {
            cmd.value: tr.result.value if tr.result != "ILLEGAL" else "ILLEGAL"
            for cmd, tr in cmds.items()
        }
        for state, cmds in _TRANSITIONS.items()
    }


def main() -> int:
    if "--table" in sys.argv:
        states = [
            ExecutionState.READY,
            ExecutionState.RUNNING,
            ExecutionState.PAUSED,
            ExecutionState.STOPPED,
        ]
        cmds = [
            CriticCommand.START,
            CriticCommand.PAUSE,
            CriticCommand.RESUME,
            CriticCommand.STOP,
        ]
        header = "State \\ Command | " + " | ".join(c.value for c in cmds)
        print(header)
        print("-" * len(header))
        for s in states:
            row = [s.value]
            for c in cmds:
                tr = _TRANSITIONS[s][c]
                cell = tr.result.value if tr.result != "ILLEGAL" else "ILLEGAL"
                if tr.already:
                    cell += "(already)"
                row.append(cell)
            print(" | ".join(row))
        return 0
    print("usage: python -m dev_harness.contracts.transitions --table")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
