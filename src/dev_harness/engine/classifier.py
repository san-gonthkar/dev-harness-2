"""E2E failure classifier with HITL fallback (V11 8.17).

When the end-to-end suite fails, the pipeline must decide *what kind* of
failure it is, because the two kinds have different remedies:

* :data:`~dev_harness.contracts.state.CHUNK_IMPLEMENTATION_BUG` - a single
  chunk's own tests fail in isolation, so the Developer must fix that chunk;
* :data:`~dev_harness.contracts.state.INTEGRATION_SPEC_MISMATCH` - every chunk
  passes in isolation but the merged result fails, so the *specification* (the
  Architect's design) is at fault, not any one chunk.

The classifier reads a **structured test report** and maps it to
``E2EReport.classification``. The report format is line-oriented and strict::

    chunk: chunk-1 status: FAIL
    chunk: chunk-2 status: PASS
    e2e: FAIL

Rules (pure function of the text):

* ``e2e: PASS`` -> no failure, ``classification`` is ``None``;
* ``e2e: FAIL`` and at least one ``chunk: ... status: FAIL`` ->
  ``CHUNK_IMPLEMENTATION_BUG`` with the first failing chunk id;
* ``e2e: FAIL`` and every chunk ``PASS`` -> ``INTEGRATION_SPEC_MISMATCH``.

**Never a guess.** If the report cannot be parsed - a malformed line, a missing
``e2e:`` verdict, or an ``e2e: FAIL`` with no chunk results to reason about -
the classifier fabricates no classification and instead returns a **HITL
route** (:data:`~dev_harness.engine.routing.HITL_NODE`) so a human decides.

The classification values are imported from ``contracts.state`` (the canonical
``Literal``), so no state string literal lives outside ``contracts``.

``engine/classifier.py`` is in the 8.C high-coverage set (95/90): every
classification path and every unparseable path is exercised by the tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dev_harness.contracts.state import (
    CHUNK_IMPLEMENTATION_BUG,
    INTEGRATION_SPEC_MISMATCH,
    E2EReport,
)
from dev_harness.engine.routing import HITL_NODE, RouteDecision
from dev_harness.engine.state import HarnessStateChannels

#: ``chunk: <id> status: PASS|FAIL`` - one chunk's isolated test verdict.
_CHUNK_RE = re.compile(r"^chunk:\s*(\S+)\s+status:\s*(PASS|FAIL)$")

#: ``e2e: PASS|FAIL`` - the merged end-to-end verdict.
_E2E_RE = re.compile(r"^e2e:\s*(PASS|FAIL)$")

_PASS = "PASS"
_FAIL = "FAIL"


@dataclass(frozen=True)
class ClassificationResult:
    """The classifier's outcome for one report (V11 8.17).

    Exactly one of :attr:`report` / :attr:`decision` is set:

    :param report: the classified :class:`E2EReport`, or ``None`` when the
        report was unparseable (or reported no failure).
    :param decision: the HITL :class:`RouteDecision` for an unparseable report,
        or ``None`` when the report parsed.
    :param unparseable: whether the report could not be parsed.
    """

    report: E2EReport | None
    decision: RouteDecision | None
    unparseable: bool


def _hitl_decision() -> RouteDecision:
    """The HITL route taken when a report cannot be classified (never a guess)."""
    return RouteDecision(next_node=HITL_NODE, escalate_to_hitl=True)


def _parse(report_text: str) -> tuple[list[tuple[str, str]], str] | None:
    """Parse ``report_text`` into ``(chunk_verdicts, e2e_verdict)`` or ``None``.

    Returns ``None`` when any non-blank line is malformed or the ``e2e:``
    verdict is missing - the caller then routes to HITL.
    """
    chunks: list[tuple[str, str]] = []
    e2e: str | None = None
    for raw_line in report_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        chunk_match = _CHUNK_RE.match(line)
        if chunk_match is not None:
            chunks.append((chunk_match.group(1), chunk_match.group(2)))
            continue
        e2e_match = _E2E_RE.match(line)
        if e2e_match is not None:
            e2e = e2e_match.group(1)
            continue
        return None
    if e2e is None:
        return None
    return chunks, e2e


def classify_report(
    report_text: str, *, state: HarnessStateChannels
) -> ClassificationResult:
    """Classify an E2E test report, routing unparseable input to HITL.

    :param report_text: the structured test report (see the module docstring).
    :param state: the graph state channels, carried for the HITL route.
    :returns: a :class:`ClassificationResult`; on unparseable input the
        ``decision`` is the HITL route and ``report`` is ``None``.
    """
    del state  # the HITL route is unconditional; state is carried for symmetry.
    parsed = _parse(report_text)
    if parsed is None:
        return ClassificationResult(
            report=None, decision=_hitl_decision(), unparseable=True
        )

    chunks, e2e = parsed
    if e2e == _PASS:
        return ClassificationResult(
            report=E2EReport(stack_trace=report_text), decision=None, unparseable=False
        )

    # e2e FAIL: an empty chunk list gives nothing to reason about -> HITL.
    if not chunks:
        return ClassificationResult(
            report=None, decision=_hitl_decision(), unparseable=True
        )

    failing = next((chunk_id for chunk_id, status in chunks if status == _FAIL), None)
    if failing is not None:
        report = E2EReport(
            classification=CHUNK_IMPLEMENTATION_BUG,
            stack_trace=report_text,
            failed_chunk_id=failing,
        )
    else:
        report = E2EReport(
            classification=INTEGRATION_SPEC_MISMATCH,
            stack_trace=report_text,
        )
    return ClassificationResult(report=report, decision=None, unparseable=False)
