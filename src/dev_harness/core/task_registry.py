"""Task registry per thread_id (V11 6.3).

The registry tracks the asyncio tasks owned by each engine thread (agent
runs, watchers, etc.) so an interrupt can cancel every task of a thread in
one call. ``cancel_all`` cancels each task and joins them with a 1s timeout
per the plan; tasks that refuse to stop are reported rather than leaked.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable

JOIN_TIMEOUT = 1.0  # seconds, per V11 6.3


class TaskRegistry:
    """A per-thread registry of asyncio tasks.

    ``register`` adds a task under a ``thread_id``; ``cancel_all`` cancels
    every task of a thread and waits up to :data:`JOIN_TIMEOUT` for each to
    finish. Tasks that do not stop within the timeout are returned so the
    caller can escalate (6.5).
    """

    def __init__(self) -> None:
        self._tasks: dict[str, set[asyncio.Task[object]]] = defaultdict(set)

    def register(self, thread_id: str, task: asyncio.Task[object]) -> None:
        """Track ``task`` under ``thread_id``."""
        self._tasks[thread_id].add(task)

    def unregister(self, thread_id: str, task: asyncio.Task[object]) -> None:
        """Stop tracking ``task`` (no-op if absent)."""
        tasks = self._tasks.get(thread_id)
        if tasks is None:
            return
        tasks.discard(task)
        if not tasks:
            del self._tasks[thread_id]

    def tasks(self, thread_id: str) -> frozenset[asyncio.Task[object]]:
        """The tasks currently tracked for ``thread_id``."""
        return frozenset(self._tasks.get(thread_id, ()))

    def count(self, thread_id: str) -> int:
        """Number of tasks tracked for ``thread_id``."""
        return len(self._tasks.get(thread_id, ()))

    def thread_ids(self) -> Iterable[str]:
        """The thread ids that currently have tracked tasks."""
        return self._tasks.keys()

    async def cancel_all(self, thread_id: str) -> list[asyncio.Task[object]]:
        """Cancel and join every task of ``thread_id``.

        Each task is cancelled, then awaited with a 1s timeout. Tasks that
        finish within the timeout are removed from the registry; tasks that
        do not are returned (still tracked) for escalation.
        """
        pending = list(self._tasks.get(thread_id, ()))
        if not pending:
            self._tasks.pop(thread_id, None)
            return []
        for task in pending:
            task.cancel()
        done, _ = await asyncio.wait(pending, timeout=JOIN_TIMEOUT)
        for task in done:
            # Suppress the CancelledError that cancel() schedules; the task
            # is finished either way.
            if not task.cancelled():
                task.exception()
            self._tasks[thread_id].discard(task)
        remaining = self._tasks.get(thread_id)
        if not remaining:
            self._tasks.pop(thread_id, None)
        return [task for task in pending if task not in done]
