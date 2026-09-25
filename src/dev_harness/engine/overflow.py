"""Context-overflow recovery: summarize-and-retry once, then escalate (V11 9.9).

A provider rejects an over-long request with
:class:`~dev_harness.contracts.errors.ContextOverflowError` (``retryable=False``).
Retrying the identical prompt is pointless, so this wrapper recovers
**exactly once**:

1. on the first overflow it **summarizes** the prompt (the default summarizer
   caps every message with :func:`~dev_harness.engine.context.cap_trace`) and
   **retries once**;
2. if the retry **also** overflows it **escalates to HITL** - reusing
   :func:`~dev_harness.engine.routing.route` so the decision is the canonical
   ``HITL_NODE`` :class:`~dev_harness.engine.routing.RouteDecision` - and never
   retries again.

The retry budget is a module constant (:data:`MAX_OVERFLOW_RETRIES` = 1), so the
loop is bounded by construction: at most two inner calls, never an infinite
loop. The client and the summarizer are injected, so tests run against a
scripted fake with no network.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from dev_harness.contracts.errors import ContextOverflowError
from dev_harness.contracts.llm import Message, Usage
from dev_harness.engine.context import cap_trace
from dev_harness.engine.personas.validators import CompletionClient
from dev_harness.engine.routing import (
    E2E_CEILING,
    FailureKind,
    RouteDecision,
    route,
)
from dev_harness.engine.state import HarnessStateChannels

#: The plan fixes the overflow recovery budget at exactly one retry (V11 9.9).
MAX_OVERFLOW_RETRIES = 1

#: Shrinks a prompt so it fits the model's context window.
Summarizer = Callable[[list[Message]], list[Message]]


def default_summarizer(messages: list[Message]) -> list[Message]:
    """Shrink each message to a bounded head/tail window (8.16 ``cap_trace``)."""
    return [
        Message(
            role=message.role, content=cap_trace(message.content), name=message.name
        )
        for message in messages
    ]


@dataclass(frozen=True)
class OverflowOutcome:
    """The result of one overflow-recovered completion (V11 9.9).

    :param text: the completion text, or ``None`` when the run escalated.
    :param usage: the token usage, or ``None`` when the run escalated.
    :param retries: the number of summarize-and-retry attempts made (0 or 1).
    :param decision: the HITL escalation decision, or ``None`` on success.
    """

    text: str | None
    usage: Usage | None
    retries: int
    decision: RouteDecision | None = None

    @property
    def escalated(self) -> bool:
        """Whether the second overflow forced a HITL escalation."""
        return self.decision is not None


class OverflowRecovery:
    """A :class:`CompletionClient` that recovers from one overflow, then escalates.

    Wraps the injected client. A successful completion costs zero retries; a
    single overflow triggers one summarize-and-retry; a second overflow returns
    a HITL escalation instead of retrying again.
    """

    def __init__(
        self,
        inner: CompletionClient,
        *,
        summarize: Summarizer = default_summarizer,
    ) -> None:
        self._inner = inner
        self._summarize = summarize
        self.retries = 0

    async def complete_with_recovery(
        self, messages: list[Message], *, model: str | None = None
    ) -> OverflowOutcome:
        """Complete ``messages``, recovering from at most one context overflow.

        Returns an :class:`OverflowOutcome`; when the retry also overflows the
        outcome carries the HITL :class:`RouteDecision` and no text.
        """
        self.retries = 0
        try:
            text, usage = await self._inner.complete(messages, model=model)
        except ContextOverflowError:
            pass
        else:
            return OverflowOutcome(text=text, usage=usage, retries=0)

        # Exactly one summarize-and-retry (MAX_OVERFLOW_RETRIES == 1).
        self.retries += 1
        summarized = self._summarize(list(messages))
        try:
            text, usage = await self._inner.complete(summarized, model=model)
        except ContextOverflowError:
            return OverflowOutcome(
                text=None,
                usage=None,
                retries=self.retries,
                decision=self._escalate(),
            )
        return OverflowOutcome(text=text, usage=usage, retries=self.retries)

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        """``CompletionClient`` surface: raise the overflow when it escalates.

        On success returns ``(text, usage)``. When the retry also overflows the
        wrapper has already computed the HITL escalation, so it re-raises the
        (non-retryable) :class:`ContextOverflowError` for the caller to route.
        """
        outcome = await self.complete_with_recovery(messages, model=model)
        if outcome.text is None or outcome.usage is None:
            raise ContextOverflowError(
                "context overflow persisted after one summarize-and-retry; "
                "escalating to HITL"
            )
        return outcome.text, outcome.usage

    def _escalate(self) -> RouteDecision:
        """The canonical HITL escalation decision, via the 8.13 router."""
        state = cast("HarnessStateChannels", {"e2e_retry_count": E2E_CEILING})
        return route(state, failure_kind=FailureKind.E2E)
