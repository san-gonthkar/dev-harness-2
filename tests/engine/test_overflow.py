"""Context-overflow recovery tests (V11 9.9).

Validation matrix (9.B, acceptance is exact):

(a) on ``ContextOverflowError`` the wrapper **summarizes** the prompt and
    **retries exactly once** (retry count == 1);
(b) if the retry **also** overflows it **escalates to HITL** (the canonical
    ``HITL_NODE`` decision from ``engine.routing.route``) instead of retrying;
(c) **0 infinite loops** - the client is called at most twice, so the test
    terminates well under ``--timeout=120``.

The client is a scripted fake (no network); the summarizer is injected.
"""

from __future__ import annotations

import pytest

from dev_harness.contracts.errors import ContextOverflowError
from dev_harness.contracts.llm import Message, Usage
from dev_harness.engine.overflow import (
    MAX_OVERFLOW_RETRIES,
    OverflowRecovery,
    default_summarizer,
)
from dev_harness.engine.routing import HITL_NODE


class ScriptedClient:
    """A deterministic fake client that overflows for scripted call indices."""

    def __init__(self, *, overflow_calls: set[int]) -> None:
        self._overflow_calls = overflow_calls
        self.calls: list[list[Message]] = []

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        index = len(self.calls)
        self.calls.append(list(messages))
        if index in self._overflow_calls:
            raise ContextOverflowError("prompt exceeds context window")
        return "ok", Usage(input_tokens=1, output_tokens=1)


def _messages() -> list[Message]:
    return [Message(role="user", content="do the thing")]


@pytest.mark.unit
def test_retry_budget_is_exactly_one() -> None:
    """The overflow recovery budget is exactly one retry (V11 9.9)."""
    assert MAX_OVERFLOW_RETRIES == 1


@pytest.mark.unit
async def test_success_costs_zero_retries() -> None:
    """A completion that never overflows costs 0 retries and one call."""
    client = ScriptedClient(overflow_calls=set())
    recovery = OverflowRecovery(client)

    outcome = await recovery.complete_with_recovery(_messages())

    assert outcome.text == "ok"
    assert outcome.retries == 0
    assert outcome.escalated is False
    assert len(client.calls) == 1


@pytest.mark.unit
async def test_first_overflow_summarizes_and_retries_once() -> None:
    """(a) One overflow -> summarize + retry exactly once, then succeed."""
    client = ScriptedClient(overflow_calls={0})
    summarized: list[list[Message]] = []

    def summarize(messages: list[Message]) -> list[Message]:
        summarized.append(list(messages))
        return [Message(role="user", content="short")]

    recovery = OverflowRecovery(client, summarize=summarize)
    outcome = await recovery.complete_with_recovery(_messages())

    assert outcome.text == "ok"
    assert outcome.retries == 1
    assert outcome.escalated is False
    assert len(client.calls) == 2
    # The retry carried the summarized prompt, not the original.
    assert summarized == [_messages()]
    assert client.calls[1] == [Message(role="user", content="short")]


@pytest.mark.negative
async def test_second_overflow_escalates_to_hitl_without_retrying() -> None:
    """(b) A second overflow escalates to HITL; the client is never called again."""
    client = ScriptedClient(overflow_calls={0, 1})
    recovery = OverflowRecovery(client)

    outcome = await recovery.complete_with_recovery(_messages())

    assert outcome.text is None
    assert outcome.usage is None
    assert outcome.retries == 1
    assert outcome.escalated is True
    assert outcome.decision is not None
    assert outcome.decision.next_node == HITL_NODE
    assert outcome.decision.escalate_to_hitl is True
    # (c) Bounded: exactly two calls, no third retry, no infinite loop.
    assert len(client.calls) == 2


@pytest.mark.negative
async def test_complete_reraises_overflow_on_escalation() -> None:
    """The ``CompletionClient`` surface re-raises the overflow when escalating."""
    client = ScriptedClient(overflow_calls={0, 1})
    recovery = OverflowRecovery(client)

    with pytest.raises(ContextOverflowError):
        await recovery.complete(_messages())

    assert len(client.calls) == 2


@pytest.mark.unit
def test_default_summarizer_caps_long_content() -> None:
    """The default summarizer shrinks an over-long message via ``cap_trace``."""
    long_content = "\n".join(f"line {i}" for i in range(200))
    summarized = default_summarizer([Message(role="user", content=long_content)])

    assert len(summarized) == 1
    assert summarized[0].role == "user"
    assert "lines elided" in summarized[0].content
    assert len(summarized[0].content.splitlines()) < 200
