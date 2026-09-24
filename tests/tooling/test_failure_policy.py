"""Tests for the typed failure recovery policy (process fix 4)."""

from __future__ import annotations

import json

import pytest

from scripts import failure_policy

pytestmark = pytest.mark.unit


def test_every_outcome_has_a_policy() -> None:
    from scripts.dispatch_log import OUTCOMES

    for outcome in OUTCOMES:
        assert outcome in failure_policy.POLICIES


def test_empty_return_is_split_not_retried() -> None:
    policy = failure_policy.policy_for("empty_return")
    assert policy.response == "split"
    assert policy.retry is False


def test_hang_is_fixed_not_retried() -> None:
    policy = failure_policy.policy_for("hang")
    assert policy.response == "fix_loop"
    assert policy.retry is False


def test_test_failure_is_retried_with_output() -> None:
    policy = failure_policy.policy_for("test_failure")
    assert policy.response == "redispatch_with_output"
    assert policy.retry is True


def test_lane_breach_is_discarded() -> None:
    policy = failure_policy.policy_for("lane_breach")
    assert policy.response == "log_and_discard"
    assert policy.retry is False


def test_unknown_class_raises() -> None:
    with pytest.raises(KeyError):
        failure_policy.policy_for("banana")


def test_main_list_prints_all(capsys: pytest.CaptureFixture[str]) -> None:
    assert failure_policy.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "empty_return" in out
    assert "hang" in out


def test_main_json_emits_policy(capsys: pytest.CaptureFixture[str]) -> None:
    assert failure_policy.main(["--class", "hang", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["response"] == "fix_loop"


def test_main_unknown_class_exits_1() -> None:
    assert failure_policy.main(["--class", "banana"]) == 1


def test_main_requires_argument() -> None:
    assert failure_policy.main([]) == 2