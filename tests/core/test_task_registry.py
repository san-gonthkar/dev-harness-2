"""Task registry tests (V11 6.3).

Validation matrix: 20 registered tasks are all ``cancelled()`` after
``cancel_all``; ``asyncio.all_tasks()`` afterwards holds only the test task
(no leaked tasks). Also covers per-thread isolation, unregister, and the
1s join timeout escalation path.
"""

from __future__ import annotations

import asyncio

import pytest

from dev_harness.core.task_registry import JOIN_TIMEOUT, TaskRegistry

pytestmark = pytest.mark.unit


async def _never_returns() -> None:
    """A task that runs until cancelled."""
    while True:
        await asyncio.sleep(3600)


async def _cooperative() -> None:
    """A task that finishes promptly when cancelled."""
    try:
        await asyncio.sleep(3600)
    except asyncio.CancelledError:
        return


async def _stubborn(stop: asyncio.Event) -> None:
    """A task that ignores cancellation until ``stop`` is set.

    The ``await asyncio.sleep(0)`` yields once per iteration so that, once
    ``stop`` is set, the next cancelled iteration observes it and returns.
    """
    while not stop.is_set():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            continue


# --- 6.B row: 20 tasks all cancelled; no leaks ------------------------------


@pytest.mark.asyncio
async def test_cancel_all_cancels_20_tasks_and_leaves_no_leaks() -> None:
    registry = TaskRegistry()
    tasks = [asyncio.create_task(_never_returns()) for _ in range(20)]
    for task in tasks:
        registry.register("thread-1", task)

    assert registry.count("thread-1") == 20

    leftover = await registry.cancel_all("thread-1")

    assert leftover == []
    assert all(t.cancelled() for t in tasks)
    assert registry.count("thread-1") == 0
    # Only the test task remains in the loop.
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert others == []


# --- per-thread isolation ---------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_all_only_touches_one_thread() -> None:
    registry = TaskRegistry()
    t1 = asyncio.create_task(_never_returns())
    t2 = asyncio.create_task(_never_returns())
    registry.register("thread-a", t1)
    registry.register("thread-b", t2)

    await registry.cancel_all("thread-a")

    assert t1.cancelled()
    assert not t2.cancelled()
    assert registry.count("thread-a") == 0
    assert registry.count("thread-b") == 1
    # Clean up the survivor.
    await registry.cancel_all("thread-b")
    assert t2.cancelled()


# --- register / unregister / tasks / thread_ids -----------------------------


@pytest.mark.asyncio
async def test_register_unregister_and_inspection() -> None:
    registry = TaskRegistry()
    task = asyncio.create_task(_cooperative())

    registry.register("t", task)
    assert registry.tasks("t") == frozenset({task})
    assert registry.count("t") == 1
    assert list(registry.thread_ids()) == ["t"]

    registry.unregister("t", task)
    assert registry.count("t") == 0
    assert list(registry.thread_ids()) == []

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_unregister_absent_task_is_noop() -> None:
    registry = TaskRegistry()
    task = asyncio.create_task(_cooperative())

    registry.unregister("t", task)  # never registered

    assert registry.count("t") == 0
    assert list(registry.thread_ids()) == []

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# --- cooperative tasks finish cleanly on cancel -----------------------------


@pytest.mark.asyncio
async def test_cancel_all_joins_cooperative_tasks() -> None:
    registry = TaskRegistry()
    task = asyncio.create_task(_cooperative())
    registry.register("t", task)

    leftover = await registry.cancel_all("t")

    assert leftover == []
    assert task.cancelled()
    assert registry.count("t") == 0


# --- 1s join timeout: stubborn tasks are returned for escalation ------------


@pytest.mark.asyncio
async def test_cancel_all_returns_stubborn_tasks_after_timeout() -> None:
    registry = TaskRegistry()
    stop = asyncio.Event()
    task = asyncio.create_task(_stubborn(stop))
    registry.register("t", task)
    # Let the coroutine reach its first await so cancellation is delivered
    # *into* a running task (a never-started task cancels instantly).
    await asyncio.sleep(0)

    leftover = await registry.cancel_all("t")

    # The task swallows CancelledError, so it survives the 1s join and is
    # returned for escalation (6.5).
    assert leftover == [task]
    assert registry.count("t") == 1
    # Clean up: set stop, then cancel to wake the parked sleep so the next
    # iteration observes stop and returns.
    stop.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# --- empty thread -----------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_all_empty_thread_returns_empty() -> None:
    registry = TaskRegistry()

    leftover = await registry.cancel_all("nobody")

    assert leftover == []
    assert JOIN_TIMEOUT == 1.0
