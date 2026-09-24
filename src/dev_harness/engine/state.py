"""LangGraph state channels and reducers for the SDLC pipeline (V11 8.3).

``HarnessStateChannels`` is the ``TypedDict`` the SDLC graph is built over.
Each channel mirrors a ``HarnessState`` field. The channels that receive
concurrent writes from parallel nodes carry an ``Annotated`` reducer:

* ``chunk_dag`` uses :func:`append_chunks` so parallel node writes append
  (two writes -> two entries) instead of last-write-wins.
* ``inner_loop_retry_count`` / ``e2e_retry_count`` use :func:`add_counters`
  so concurrent increments sum (two increments -> exactly +2).

Every other channel is last-write-wins, mirroring the scalar semantics of
``HarnessState``.
"""

from __future__ import annotations

import operator
from typing import Annotated, Required, TypedDict, cast

from dev_harness.contracts.state import (
    Chunk,
    E2EReport,
    GitState,
    GroomedRequirements,
    RateLimiting,
    TechnicalDesign,
    TuiState,
)


def append_chunks(left: list[Chunk], right: list[Chunk]) -> list[Chunk]:
    """Append reducer for the ``chunk_dag`` channel (``operator.add``)."""
    return cast("list[Chunk]", operator.add(left, right))


def add_counters(left: int, right: int) -> int:
    """Additive reducer for the retry-counter channels (``operator.add``)."""
    return cast("int", operator.add(left, right))


class HarnessStateChannels(TypedDict, total=False):
    """LangGraph channel schema mirroring ``HarnessState`` (V11 8.3).

    Marked ``total=False`` so a node may write a partial update; the three
    identity channels carry ``Required`` to mirror ``HarnessState``'s
    required fields.
    """

    project_id: Required[str]
    workspace_path: Required[str]
    thread_id: Required[str]
    raw_input: str
    groomed_requirements: GroomedRequirements | None
    technical_design: TechnicalDesign | None
    chunk_dag: Annotated[list[Chunk], append_chunks]
    tui_state: TuiState
    git_state: GitState
    rate_limiting: RateLimiting
    inner_loop_retry_count: Annotated[int, add_counters]
    e2e_retry_count: Annotated[int, add_counters]
    latest_e2e_report: E2EReport | None
