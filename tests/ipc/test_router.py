"""Pub/sub router tests (V11 2.5)."""

from __future__ import annotations

import logging

import pytest

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope, FileChangePayload
from dev_harness.ipc.router import EventRouter

pytestmark = pytest.mark.unit


def _env() -> Envelope:
    return Envelope(
        type=EventType.FILE_CHANGE,
        payload=FileChangePayload(
            type="FILE_CHANGE", path="/a", change_type="modified"
        ),
    )


def test_three_subscribers_each_get_one_copy() -> None:
    router = EventRouter()
    received: list[list[Envelope]] = [[], [], []]

    def make(idx: int):
        def handler(env: Envelope) -> None:
            received[idx].append(env)

        return handler

    for i in range(3):
        router.subscribe(EventType.FILE_CHANGE, make(i))
    env = _env()
    router.publish(env)
    assert all(len(r) == 1 for r in received)
    assert all(r[0] == env for r in received)


def test_raising_handler_does_not_block_others() -> None:
    router = EventRouter()
    calls: list[str] = []

    def boom(env: Envelope) -> None:
        calls.append("boom")
        raise RuntimeError("handler failed")

    def ok(env: Envelope) -> None:
        calls.append("ok")

    router.subscribe(EventType.FILE_CHANGE, boom)
    router.subscribe(EventType.FILE_CHANGE, ok)
    router.publish(_env())
    assert calls == ["boom", "ok"]  # ok still ran


def test_error_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    router = EventRouter()
    with caplog.at_level(logging.ERROR, logger="dev_harness.ipc.router"):
        router.subscribe(
            EventType.FILE_CHANGE, lambda env: (_ for _ in ()).throw(RuntimeError("x"))
        )
        router.publish(_env())
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) == 1


def test_unsubscribe_removes_handler() -> None:
    router = EventRouter()
    calls: list[str] = []

    def handler(env: Envelope) -> None:
        calls.append("x")

    router.subscribe(EventType.FILE_CHANGE, handler)
    router.publish(_env())
    assert len(calls) == 1
    router.unsubscribe(EventType.FILE_CHANGE, handler)
    router.publish(_env())
    assert len(calls) == 1  # no more calls


def test_unsubscribe_absent_is_noop() -> None:
    router = EventRouter()

    def handler(env: Envelope) -> None:
        pass

    router.unsubscribe(EventType.FILE_CHANGE, handler)  # no-op
    assert router.subscriber_count(EventType.FILE_CHANGE) == 0


def test_subscriber_count() -> None:
    router = EventRouter()
    router.subscribe(EventType.FILE_CHANGE, lambda env: None)
    router.subscribe(EventType.FILE_CHANGE, lambda env: None)
    assert router.subscriber_count(EventType.FILE_CHANGE) == 2
    assert router.subscriber_count(EventType.SNAPSHOT) == 0
