"""Persona template lint tests (V11 8.1).

Validation matrix (8.B): each persona file declares an ``## Output Contract``
heading and refusal behavior; the Critic persona is forbidden from editing code
or requirements (keyword scan); each file names its role.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PERSONA_DIR = _REPO_ROOT / "src" / "dev_harness" / "engine" / "personas"

_ROLES = ("groomer", "architect", "developer", "tester", "critic")

# Affirmative permission to edit code or requirements. The negative lookbehinds
# keep prohibitions ("may not write code", "never edit code") from matching.
_PERMISSION_PATTERNS = (
    re.compile(
        r"\b(may|can|is allowed to|is permitted to|are allowed to|are permitted to)"
        r"\s+(edit|modify|write|change|patch|create|delete|refactor)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(permission|authorized|allowed|permitted)\s+to\s+"
        r"(edit|modify|write|change|patch|create|delete|refactor)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<!not )(?<!never )\b(edit|modify|write|change|patch|refactor)\s+"
        r"(the\s+)?(code|source|requirements|implementation)\b",
        re.IGNORECASE,
    ),
)

# A line carrying any of these is a prohibition/refusal clause, not a grant of
# permission (e.g. "never edit code", "asks you to edit code" -> refuse).
_PROHIBITION_MARKERS = (
    "must not",
    "may not",
    "never",
    "forbidden",
    "read-only",
    "refuse",
    "asked you to",
    "asks you to",
    "rejected",
)


def _read(role: str) -> str:
    return (_PERSONA_DIR / f"{role}.md").read_text(encoding="utf-8")


def _is_prohibition(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in _PROHIBITION_MARKERS)


@pytest.mark.unit
@pytest.mark.parametrize("role", _ROLES)
def test_persona_file_exists(role: str) -> None:
    assert (_PERSONA_DIR / f"{role}.md").is_file()


@pytest.mark.unit
@pytest.mark.parametrize("role", _ROLES)
def test_persona_declares_output_contract(role: str) -> None:
    assert "## Output Contract" in _read(role)


@pytest.mark.unit
@pytest.mark.parametrize("role", _ROLES)
def test_persona_declares_refusal_behavior(role: str) -> None:
    assert "refusal" in _read(role).lower()


@pytest.mark.unit
@pytest.mark.parametrize("role", _ROLES)
def test_persona_names_its_role(role: str) -> None:
    assert role in _read(role).lower()


@pytest.mark.unit
def test_critic_forbids_code_edits() -> None:
    text = _read("critic")
    for line in text.splitlines():
        if _is_prohibition(line):
            continue
        for pattern in _PERMISSION_PATTERNS:
            match = pattern.search(line)
            assert match is None, (
                f"critic persona permits editing on line {line!r}: {match.group(0)!r}"
            )
    lowered = text.lower()
    assert any(marker in lowered for marker in _PROHIBITION_MARKERS)
