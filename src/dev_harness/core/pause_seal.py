"""Pause seal: is_paused + timestamp + checkpoint hash bound to git HEAD (V11 6.6).

A pause must leave a durable, verifiable record: the state is marked paused,
the moment is stamped, and the checkpoint hash is bound to the workspace's
current git HEAD so a resume can prove it restores the exact commit that was
paused.

Reuse (do not reinvent):

- ``GitAdapter.head_sha()`` (1.9) supplies the default bound hash.
- ``CheckpointBinding.put_bound`` (1.11) writes checkpoints bound to HEAD; the
  seal's hash is that same HEAD value, so a sealed checkpoint and its seal
  agree.
- ``HarnessState.tui_state.is_paused`` is the canonical paused flag.

The hash provider and clock are injectable so the seal is testable without a
real git repository; the default provider reads HEAD from the workspace.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dev_harness.contracts.state import HarnessState
from dev_harness.vcs.git import GitAdapter

HashProvider = Callable[[], str]
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class PauseSeal:
    """An immutable record of one pause: paused flag, timestamp, bound hash."""

    is_paused: bool
    timestamp: float
    checkpoint_hash: str


class PauseSealer:
    """Produces pause seals bound to the workspace's current git HEAD."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        clock: Clock | None = None,
        hash_provider: HashProvider | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self._clock = clock if clock is not None else time.time
        self._hash_provider = hash_provider

    def seal(self) -> PauseSeal:
        """Seal the current moment: paused, stamped, bound to HEAD."""
        return PauseSeal(
            is_paused=True,
            timestamp=self._clock(),
            checkpoint_hash=self._resolve_hash(),
        )

    def apply(self, state: HarnessState) -> HarnessState:
        """Return a copy of ``state`` marked paused and bound to the seal hash.

        The input state is never mutated; the returned copy has
        ``tui_state.is_paused=True`` and ``git_state.last_checkpoint_commit``
        set to the seal's bound hash.
        """
        return self.apply_seal(state, self.seal())

    @staticmethod
    def apply_seal(state: HarnessState, seal: PauseSeal) -> HarnessState:
        """Return a copy of ``state`` carrying ``seal`` (no mutation)."""
        updated = state.model_copy(deep=True)
        updated.tui_state.is_paused = seal.is_paused
        updated.git_state.last_checkpoint_commit = seal.checkpoint_hash
        return updated

    def _resolve_hash(self) -> str:
        """The injected hash provider, or HEAD of the workspace repo."""
        if self._hash_provider is not None:
            return self._hash_provider()
        return GitAdapter(self.workspace).head_sha()
