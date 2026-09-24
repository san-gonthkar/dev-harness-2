"""Context budgeting: trace capping and prompt-token budgets (V11 8.16).

Two guarantees:

* :func:`cap_trace` trims an arbitrarily long trace to a bounded head/tail
  window that always preserves the first and last frames, with an elision
  marker recording how many lines were dropped.
* :func:`build_prompt` shapes messages for a registry ``ModelEntry`` so the
  estimated prompt tokens never exceed ``context_window - max_output``.

Pure functions only: no I/O, no network, no clock.
"""

from __future__ import annotations

from collections.abc import Sequence

from dev_harness.contracts.llm import Message
from dev_harness.providers.registry import ModelEntry
from dev_harness.providers.tokenizer import CHARS_PER_TOKEN, MARGIN, estimate_tokens

# The head/tail window that bounds every trace handed to a model.
TRACE_HEAD = 30
TRACE_TAIL = 20


def cap_trace(text: str, *, head: int = TRACE_HEAD, tail: int = TRACE_TAIL) -> str:
    """Cap ``text`` to a head/tail window, keeping first and last frames.

    A trace of at most ``head + tail`` lines is returned unchanged. Otherwise
    the first ``head`` lines, a single elision-marker line recording the number
    of dropped lines, and the last ``tail`` lines are returned.
    """
    if head < 0 or tail < 0:
        raise ValueError("head and tail must be non-negative")
    lines = text.splitlines()
    if len(lines) <= head + tail:
        return text
    elided = len(lines) - head - tail
    marker = f"... [{elided} lines elided] ..."
    return "\n".join((*lines[:head], marker, *lines[-tail:]))


def prompt_budget(entry: ModelEntry) -> int:
    """Maximum prompt tokens allowed for ``entry``: context minus output."""
    return entry.context_window - entry.max_output


def fits_budget(prompt_tokens: int, entry: ModelEntry) -> bool:
    """Whether a prompt of ``prompt_tokens`` fits ``entry``'s prompt budget."""
    return prompt_tokens <= prompt_budget(entry)


def count_messages(messages: Sequence[Message]) -> int:
    """Estimated token count of a message list (content only)."""
    return sum(estimate_tokens(message.content) for message in messages)


def _clip(content: str, allowance: int) -> str:
    """Clip ``content`` so its estimated token count is at most ``allowance``."""
    if allowance <= 0:
        return ""
    if estimate_tokens(content) <= allowance:
        return content
    limit = int(allowance / (1 + MARGIN)) * CHARS_PER_TOKEN
    return content[:limit]


def build_prompt(
    messages: Sequence[Message],
    *,
    entry: ModelEntry,
    trace: str | None = None,
    head: int = TRACE_HEAD,
    tail: int = TRACE_TAIL,
) -> list[Message]:
    """Build a prompt bounded by ``entry``'s prompt budget.

    An optional ``trace`` is capped and appended as a user frame. Every message
    content is clipped when the estimated total would exceed
    ``context_window - max_output``, so the result always fits ``entry``.
    """
    prepared = list(messages)
    if trace is not None:
        capped = cap_trace(trace, head=head, tail=tail)
        prepared.append(Message(role="user", content=capped))
    budget = prompt_budget(entry)
    if count_messages(prepared) <= budget:
        return [Message(role=m.role, content=m.content, name=m.name) for m in prepared]
    share = max(1, budget // max(1, len(prepared)))
    return [
        Message(role=m.role, content=_clip(m.content, share), name=m.name)
        for m in prepared
    ]
