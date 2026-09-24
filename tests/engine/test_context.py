"""Context budgeting tests (V11 8.16).

Validation matrix (8.B): a 500-line trace is capped to a 50-frame head/tail
window containing both the first and last frames; every prompt built for any
registry model is <= context_window - max_output.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dev_harness.config import HarnessConfig, ProviderConfig
from dev_harness.contracts.llm import Message
from dev_harness.engine.context import (
    TRACE_HEAD,
    TRACE_TAIL,
    build_prompt,
    cap_trace,
    count_messages,
    fits_budget,
    prompt_budget,
)
from dev_harness.providers.registry import ModelRegistry
from dev_harness.providers.tokenizer import estimate_tokens

_ELIDED_500 = 500 - TRACE_HEAD - TRACE_TAIL


def _registry() -> ModelRegistry:
    return ModelRegistry(
        HarnessConfig(
            providers={
                "anthropic": ProviderConfig(context_window=200000),
                "openrouter": ProviderConfig(context_window=128000),
                "ollama": ProviderConfig(context_window=8192, num_ctx=8192),
            }
        )
    )


def _trace(lines: int) -> str:
    return "\n".join(f"frame {i}: at module_{i}.py:{i}" for i in range(lines))


# --- cap_trace ---------------------------------------------------------------


@pytest.mark.unit
def test_cap_trace_500_lines_to_50_frames() -> None:
    out = cap_trace(_trace(500))
    frames = out.splitlines()
    # 30 head frames + 1 elision marker + 20 tail frames.
    assert len(frames) == TRACE_HEAD + 1 + TRACE_TAIL
    assert frames[:TRACE_HEAD] == _trace(500).splitlines()[:TRACE_HEAD]
    assert frames[-TRACE_TAIL:] == _trace(500).splitlines()[-TRACE_TAIL:]


@pytest.mark.unit
def test_cap_trace_preserves_first_and_last_frames() -> None:
    out = cap_trace(_trace(500))
    frames = out.splitlines()
    assert frames[0] == "frame 0: at module_0.py:0"
    assert frames[-1] == "frame 499: at module_499.py:499"


@pytest.mark.unit
def test_cap_trace_elision_marker_between_head_and_tail() -> None:
    frames = cap_trace(_trace(500)).splitlines()
    marker = frames[TRACE_HEAD]
    assert "elided" in marker
    assert str(_ELIDED_500) in marker


@pytest.mark.unit
def test_cap_trace_short_text_unchanged() -> None:
    text = _trace(TRACE_HEAD + TRACE_TAIL)
    assert cap_trace(text) == text


@pytest.mark.unit
def test_cap_trace_exact_boundary_unchanged() -> None:
    text = _trace(TRACE_HEAD + TRACE_TAIL)
    assert len(cap_trace(text).splitlines()) == TRACE_HEAD + TRACE_TAIL


@pytest.mark.negative
def test_cap_trace_negative_window_rejected() -> None:
    with pytest.raises(ValueError):
        cap_trace(_trace(10), head=-1)


@pytest.mark.property
@given(
    st.lists(
        st.text(
            alphabet=st.characters(
                blacklist_categories=("Cc", "Zl", "Zp"), blacklist_characters="\r\n"
            ),
            min_size=1,
        ),
        min_size=1,
        max_size=200,
    )
)
@settings(max_examples=50)
def test_cap_trace_property_keeps_endpoints(lines: list[str]) -> None:
    text = "\n".join(lines)
    frames = cap_trace(text).splitlines()
    assert frames[0] == lines[0]
    assert frames[-1] == lines[-1]
    assert len(frames) <= TRACE_HEAD + 1 + TRACE_TAIL


# --- budget ------------------------------------------------------------------


@pytest.mark.unit
def test_prompt_budget_is_context_minus_output() -> None:
    registry = _registry()
    entry = registry.resolve("anthropic-default")
    assert prompt_budget(entry) == entry.context_window - entry.max_output


@pytest.mark.unit
def test_fits_budget_at_boundary() -> None:
    entry = _registry().resolve("anthropic-default")
    assert fits_budget(prompt_budget(entry), entry) is True


@pytest.mark.negative
def test_fits_budget_over_boundary_rejected() -> None:
    entry = _registry().resolve("anthropic-default")
    assert fits_budget(prompt_budget(entry) + 1, entry) is False


@pytest.mark.unit
def test_every_registry_model_prompt_fits() -> None:
    registry = _registry()
    models = registry.all_models()
    assert models
    huge = "\n".join(f"frame {i}: " + "x" * 500 for i in range(500))
    messages = [
        Message(role="system", content="y" * 100_000),
        Message(role="user", content="z" * 100_000),
    ]
    for model_id in models:
        entry = registry.resolve(model_id)
        built = build_prompt(messages, entry=entry, trace=huge)
        assert count_messages(built) <= prompt_budget(entry), model_id


@pytest.mark.unit
def test_build_prompt_caps_appended_trace() -> None:
    entry = _registry().resolve("anthropic-default")
    built = build_prompt(
        [Message(role="user", content="go")], entry=entry, trace=_trace(500)
    )
    last = built[-1]
    assert len(last.content.splitlines()) == TRACE_HEAD + 1 + TRACE_TAIL
    assert last.content.splitlines()[0] == "frame 0: at module_0.py:0"


@pytest.mark.unit
def test_build_prompt_clips_oversized_message() -> None:
    entry = _registry().resolve("qwen2.5-coder:7b")
    built = build_prompt([Message(role="user", content="z" * 1_000_000)], entry=entry)
    assert len(built) == 1
    assert estimate_tokens(built[0].content) <= prompt_budget(entry)
    assert built[0].role == "user"
