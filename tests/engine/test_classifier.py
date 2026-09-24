"""E2E failure classifier tests (V11 8.17).

Validation matrix (8.B, acceptance is exact):

(a) **10 labelled fixtures classify 100% correctly** - every fixture's expected
    classification (and failing chunk id) is asserted;
(b) an **unparseable report routes to HITL and NEVER guesses** - the HITL route
    is asserted and the result carries no fabricated classification.

``engine/classifier.py`` is in the 8.C high-coverage set (95/90): every
classification path (pass / chunk bug / spec mismatch) and every unparseable
path (malformed line, missing verdict, no chunk results) is exercised. No
``time.sleep``, no network.
"""

from __future__ import annotations

from typing import get_args

import pytest

from dev_harness.contracts.state import (
    CHUNK_IMPLEMENTATION_BUG,
    INTEGRATION_SPEC_MISMATCH,
    E2EReport,
)
from dev_harness.engine.classifier import ClassificationResult, classify_report
from dev_harness.engine.routing import HITL_NODE
from dev_harness.engine.state import HarnessStateChannels

#: (label, report text, expected classification, expected failed_chunk_id).
LABELLED_FIXTURES: list[tuple[str, str, str | None, str | None]] = [
    (
        "all-pass",
        "chunk: chunk-1 status: PASS\nchunk: chunk-2 status: PASS\ne2e: PASS\n",
        None,
        None,
    ),
    (
        "single-chunk-fails",
        "chunk: chunk-1 status: FAIL\ne2e: FAIL\n",
        CHUNK_IMPLEMENTATION_BUG,
        "chunk-1",
    ),
    (
        "second-chunk-fails",
        "chunk: chunk-1 status: PASS\nchunk: chunk-2 status: FAIL\ne2e: FAIL\n",
        CHUNK_IMPLEMENTATION_BUG,
        "chunk-2",
    ),
    (
        "all-chunks-pass-e2e-fails",
        "chunk: chunk-1 status: PASS\nchunk: chunk-2 status: PASS\ne2e: FAIL\n",
        INTEGRATION_SPEC_MISMATCH,
        None,
    ),
    (
        "first-of-two-failing-chunks",
        "chunk: chunk-1 status: FAIL\nchunk: chunk-2 status: FAIL\ne2e: FAIL\n",
        CHUNK_IMPLEMENTATION_BUG,
        "chunk-1",
    ),
    (
        "three-chunks-middle-fails",
        (
            "chunk: a status: PASS\nchunk: b status: FAIL\nchunk: c status: PASS\n"
            "e2e: FAIL\n"
        ),
        CHUNK_IMPLEMENTATION_BUG,
        "b",
    ),
    (
        "e2e-pass-with-failing-chunk",
        "chunk: chunk-1 status: FAIL\ne2e: PASS\n",
        None,
        None,
    ),
    (
        "e2e-pass-no-chunks",
        "e2e: PASS\n",
        None,
        None,
    ),
    (
        "many-chunks-all-pass-e2e-fails",
        (
            "chunk: a status: PASS\nchunk: b status: PASS\nchunk: c status: PASS\n"
            "e2e: FAIL\n"
        ),
        INTEGRATION_SPEC_MISMATCH,
        None,
    ),
    (
        "blank-lines-and-padding",
        "\n  chunk: chunk-9 status: FAIL  \n\n  e2e: FAIL  \n\n",
        CHUNK_IMPLEMENTATION_BUG,
        "chunk-9",
    ),
]

#: Reports that cannot be parsed - each must route to HITL, never a guess.
UNPARSEABLE_REPORTS: list[tuple[str, str]] = [
    ("empty", ""),
    ("whitespace-only", "   \n\n  "),
    ("missing-e2e-verdict", "chunk: chunk-1 status: PASS\n"),
    ("malformed-line", "chunk: chunk-1 status: MAYBE\ne2e: FAIL\n"),
    ("garbage", "not a report at all\n"),
    ("e2e-fail-no-chunks", "e2e: FAIL\n"),
    ("unknown-directive", "chunk: chunk-1 status: PASS\nfoo: bar\ne2e: FAIL\n"),
]


def _state() -> HarnessStateChannels:
    """A minimal state carrying only the identity channels."""
    return {"project_id": "p", "workspace_path": "w", "thread_id": "t"}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("label", "report_text", "expected_classification", "expected_chunk"),
    LABELLED_FIXTURES,
    ids=[fixture[0] for fixture in LABELLED_FIXTURES],
)
def test_labelled_fixtures_classify_correctly(
    label: str,
    report_text: str,
    expected_classification: str | None,
    expected_chunk: str | None,
) -> None:
    """(a) Each of the 10 labelled fixtures classifies exactly as labelled."""
    result = classify_report(report_text, state=_state())
    assert result.unparseable is False, label
    assert result.decision is None, label
    assert result.report is not None, label
    assert result.report.classification == expected_classification, label
    assert result.report.failed_chunk_id == expected_chunk, label


@pytest.mark.unit
def test_all_ten_labelled_fixtures_are_present() -> None:
    """The acceptance set is exactly 10 labelled fixtures."""
    assert len(LABELLED_FIXTURES) == 10


@pytest.mark.unit
def test_constants_match_the_e2e_report_literal() -> None:
    """The canonical constants are exactly the ``E2EReport`` Literal values."""
    annotation = E2EReport.model_fields["classification"].annotation
    literal = next(arg for arg in get_args(annotation) if arg is not type(None))
    assert set(get_args(literal)) == {
        CHUNK_IMPLEMENTATION_BUG,
        INTEGRATION_SPEC_MISMATCH,
    }


@pytest.mark.unit
def test_stack_trace_is_preserved() -> None:
    """The raw report is carried through as the report's stack trace."""
    text = "chunk: chunk-1 status: FAIL\ne2e: FAIL\n"
    result = classify_report(text, state=_state())
    assert result.report is not None
    assert result.report.stack_trace == text


@pytest.mark.negative
@pytest.mark.parametrize(
    ("label", "report_text"),
    UNPARSEABLE_REPORTS,
    ids=[case[0] for case in UNPARSEABLE_REPORTS],
)
def test_unparseable_report_routes_to_hitl_never_guesses(
    label: str, report_text: str
) -> None:
    """(b) An unparseable report routes to HITL and fabricates no classification."""
    result: ClassificationResult = classify_report(report_text, state=_state())
    assert result.unparseable is True, label
    assert result.report is None, label
    assert result.decision is not None, label
    assert result.decision.next_node == HITL_NODE, label
    assert result.decision.escalate_to_hitl is True, label
