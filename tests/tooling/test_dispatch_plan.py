"""Tests for the dependency-aware dispatch planner (process fix 7)."""

from __future__ import annotations

import json

import pytest

from scripts import dispatch_plan

pytestmark = pytest.mark.unit


def test_prereqs_parsed_for_phase_7() -> None:
    prereqs = dispatch_plan._prereqs_by_task("7")
    assert prereqs["7.2"] == ["7.1", "1.9"]
    assert prereqs["7.1"] == ["5.4"]


def test_wave_1_has_only_tasks_with_no_in_phase_prereqs() -> None:
    waves = dispatch_plan.build_plan("7", done=set())
    assert waves[0].wave == 1
    assert "7.1" in waves[0].ready
    assert "7.2" not in waves[0].ready


def test_completed_tasks_are_excluded() -> None:
    done = {"7.1", "7.12"}
    waves = dispatch_plan.build_plan("7", done=done)
    all_tasks = {t for w in waves for t in w.ready}
    assert "7.1" not in all_tasks
    assert "7.12" not in all_tasks


def test_disjoint_files_are_parallel_safe() -> None:
    waves = dispatch_plan.build_plan("7", done=set())
    # 7.1 (app.py/app.tcss) and 7.12 (verify_phase_07.sh) touch disjoint files.
    wave1 = waves[0]
    assert set(wave1.parallel_safe) == {"7.1", "7.12"}
    assert wave1.serialized == []


def test_all_tasks_appear_exactly_once_across_waves() -> None:
    waves = dispatch_plan.build_plan("7", done=set())
    seen = [t for w in waves for t in w.ready]
    assert len(seen) == len(set(seen))
    assert len(seen) == 13


def test_waves_are_topological() -> None:
    waves = dispatch_plan.build_plan("7", done=set())
    placed: set[str] = set()
    for wave in waves:
        for task in wave.ready:
            prereqs = dispatch_plan._prereqs_by_task("7").get(task, [])
            for prereq in prereqs:
                if prereq.startswith("7."):
                    assert prereq in placed, f"{task} ran before its prereq {prereq}"
        placed |= set(wave.ready)


def test_main_json_emits_waves(capsys: pytest.CaptureFixture[str]) -> None:
    assert dispatch_plan.main(["--phase", "7", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert payload[0]["wave"] == 1