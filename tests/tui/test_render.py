"""Safe renderer tests (V11 task 7.10).

The contract (7.B): ``[bold red]`` renders literally and no ``MarkupError`` is
ever raised. Unit tests assert exact ``Text.plain`` values and inspect style
spans; negative tests fuzz a table of hostile inputs. No ``time.sleep``.
"""

from __future__ import annotations

import pytest
from rich.console import Console
from rich.errors import MarkupError
from rich.text import Text

from dev_harness.tui.render import (
    DIFF_ADDED_STYLE,
    DIFF_HUNK_STYLE,
    DIFF_REMOVED_STYLE,
    render_diff,
    render_markdown,
    safe_text,
)

#: Raw markup that must survive verbatim.
MARKUP = "[bold red]"
#: Hostile inputs that must never raise and must round-trip literally.
NASTY = [
    "",
    "[",
    "[]",
    "[/]",
    "[[",
    "[bold",
    "[bold red]",
    "[link=http://x]y[/link]",
    "[red]x[/red]",
    "a[b]c",
    "\\[x]",
    "[bold][/bold]",
    "[bold red]x[/]",
    "[/",
    "[#fff]",
    "[on red]",
    "[notacolor]",
    "[bold red]]",
    "[[]]",
    "[a][b][c]",
    "[bold]x[/bold]y[/bold]z",
    "café — 日本語",
    "[bold red] 日本",
]

DIFF = (
    "--- a/f.py\n"
    "+++ b/f.py\n"
    "@@ -1,2 +1,2 @@\n"
    "-old [bold]\n"
    "+new [red]\n"
    " context\n"
)


def _style_at(text: Text, index: int) -> str:
    """Return the style name covering ``index`` (empty when unstyled)."""
    for span in text.spans:
        if span.start <= index < span.end:
            return str(span.style)
    return ""


# --- unit -------------------------------------------------------------------


@pytest.mark.unit
def test_safe_text_returns_text_and_roundtrips_markup() -> None:
    out = safe_text(MARKUP)
    assert isinstance(out, Text)
    assert out.plain == MARKUP
    assert out.spans == []


@pytest.mark.unit
def test_safe_text_roundtrips_plain_text() -> None:
    out = safe_text("hello world")
    assert out.plain == "hello world"
    assert out.spans == []


@pytest.mark.unit
def test_render_markdown_renders_markup_literally() -> None:
    console = Console(record=True, width=120)
    console.print(render_markdown(MARKUP))
    assert MARKUP in console.export_text()


@pytest.mark.unit
def test_render_markdown_preserves_structure() -> None:
    console = Console(record=True, width=120)
    console.print(render_markdown("# Title\n\nbody"))
    assert "Title" in console.export_text()


@pytest.mark.unit
def test_render_markdown_empty_is_safe() -> None:
    console = Console(record=True, width=120)
    console.print(render_markdown(""))
    assert console.export_text().strip() == ""


# --- unit (diff) ------------------------------------------------------------


@pytest.mark.unit
def test_render_diff_returns_text_preserving_plain() -> None:
    out = render_diff(DIFF)
    assert isinstance(out, Text)
    assert out.plain == DIFF


@pytest.mark.unit
def test_render_diff_colours_added_removed_and_hunk() -> None:
    out = render_diff(DIFF)
    assert _style_at(out, out.plain.index("@@ -1,2")) == DIFF_HUNK_STYLE
    assert _style_at(out, out.plain.index("-old [bold]")) == DIFF_REMOVED_STYLE
    assert _style_at(out, out.plain.index("+new [red]")) == DIFF_ADDED_STYLE


@pytest.mark.unit
def test_render_diff_keeps_markup_literal() -> None:
    out = render_diff(DIFF)
    assert "[bold]" in out.plain
    assert "[red]" in out.plain
    assert " context" in out.plain


@pytest.mark.unit
def test_render_diff_empty_is_empty_text() -> None:
    out = render_diff("")
    assert out.plain == ""
    assert out.spans == []


# --- negative ---------------------------------------------------------------


@pytest.mark.negative
@pytest.mark.parametrize("raw", NASTY)
def test_safe_text_never_raises_and_is_literal(raw: str) -> None:
    try:
        out = safe_text(raw)
    except MarkupError:  # pragma: no cover - the defect this test guards
        pytest.fail(f"safe_text raised MarkupError for {raw!r}")
    assert out.plain == raw


@pytest.mark.negative
@pytest.mark.parametrize("raw", NASTY)
def test_render_markdown_never_raises(raw: str) -> None:
    console = Console(record=True, width=120)
    try:
        console.print(render_markdown(raw))
    except MarkupError:  # pragma: no cover - the defect this test guards
        pytest.fail(f"render_markdown raised MarkupError for {raw!r}")


@pytest.mark.negative
@pytest.mark.parametrize("raw", NASTY)
def test_render_markdown_visible_text_equals_input(raw: str) -> None:
    console = Console(record=True, width=120)
    console.print(render_markdown(raw))
    visible = console.export_text()
    if raw:
        assert raw in visible
    else:
        assert visible.strip() == ""


@pytest.mark.negative
@pytest.mark.parametrize("raw", NASTY)
def test_render_diff_never_raises(raw: str) -> None:
    try:
        out = render_diff(raw)
    except MarkupError:  # pragma: no cover - the defect this test guards
        pytest.fail(f"render_diff raised MarkupError for {raw!r}")
    assert out.plain == raw
