"""Safe renderer for untrusted LLM text (V11 task 7.10).

LLM output is untrusted: if it contains Rich markup such as ``[bold red]`` the
console would interpret it as styling, and a malformed tag (``[/]``) would raise
:class:`~rich.errors.MarkupError`. This module neutralises that by routing every
piece of untrusted text through :func:`rich.markup.escape` *before* it reaches a
markup-parsing API, so brackets render literally and no ``MarkupError`` can
escape.

Contract (7.B): ``[bold red]`` renders literally and no ``MarkupError`` is ever
raised, for any input. The module is pure rendering — it imports only ``rich``
and never touches ``engine/``.

The escaping primitive is :func:`safe_text`; :func:`render_markdown` and
:func:`render_diff` build on it.

Every entry point also routes its input through
:func:`dev_harness.observability.redact.redact` (V11 task 9.7), so a secret in
untrusted text is masked *before* it reaches a RichLog buffer. Redaction is
idempotent and a no-op for secret-free text, so the 7.B escaping contract is
unchanged.
"""

from __future__ import annotations

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.markup import escape
from rich.text import Text

from dev_harness.observability.redact import redact

__all__ = ["render_diff", "render_markdown", "safe_text"]

#: Style applied to added lines in a unified diff.
DIFF_ADDED_STYLE = "green"
#: Style applied to removed lines in a unified diff.
DIFF_REMOVED_STYLE = "red"
#: Style applied to ``@@`` hunk headers in a unified diff.
DIFF_HUNK_STYLE = "cyan"
#: Style applied to file headers, context lines, and everything else.
DIFF_MUTED_STYLE = "dim"


def safe_text(text: str) -> Text:
    """Return ``text`` as literal Rich :class:`~rich.text.Text`.

    Rich markup in ``text`` is escaped (``[`` -> ``\\[``) before parsing, so the
    markup characters survive verbatim in :attr:`Text.plain` and no style spans
    are created from them. Never raises ``MarkupError``: ``escape`` is total and
    ``Text.from_markup`` then only ever sees bracket-escaped input.

    Secrets are redacted first (V11 9.7), so :attr:`Text.plain` equals
    ``redact(text)`` rather than ``text`` when a key is present.
    """
    return Text.from_markup(escape(redact(text)))


def render_markdown(text: str) -> RenderableType:
    """Render untrusted ``text`` as Markdown without interpreting Rich markup.

    Markdown structure (headings, code fences, emphasis) is parsed, but the
    source is bracket-escaped first, so a Rich tag like ``[bold red]`` stays
    literal instead of becoming a style. Never raises ``MarkupError``. Secrets
    are redacted before parsing (V11 9.7).
    """
    return Markdown(escape(redact(text)))


def _line_style(line: str) -> str:
    """Classify one unified-diff line to its display style."""
    if line.startswith("@@"):
        return DIFF_HUNK_STYLE
    if line.startswith(("+++", "---")):
        return DIFF_MUTED_STYLE
    if line.startswith("+"):
        return DIFF_ADDED_STYLE
    if line.startswith("-"):
        return DIFF_REMOVED_STYLE
    return DIFF_MUTED_STYLE


def render_diff(diff_text: str) -> Text:
    """Colourise a unified diff, escaping content so markup stays literal.

    Added lines (``+``) are green, removed lines (``-``) red, ``@@`` hunk
    headers cyan, and file headers/context lines dim. Each line is escaped via
    :func:`safe_text`, so markup inside a diff line renders literally and the
    concatenated :attr:`Text.plain` equals ``redact(diff_text)`` exactly (V11
    9.7 redacts the whole diff before splitting, so a key is masked even when it
    spans a line boundary).
    """
    out = Text()
    for index, line in enumerate(redact(diff_text).split("\n")):
        if index:
            out.append("\n")
        segment = safe_text(line)
        segment.stylize(_line_style(line))
        out.append_text(segment)
    return out
