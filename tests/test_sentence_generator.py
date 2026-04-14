"""Tests for skills/sentence_generator.py (ADR-017)."""

from __future__ import annotations

from unittest.mock import MagicMock

from klemma.ai import AICallResult
from klemma.skills.sentence_generator import (
    _extract_json_object,
    _normalize_authors,
    generate_sentences,
)

# ---------------------------------------------------------------------------
# _normalize_authors
# ---------------------------------------------------------------------------


def test_normalize_authors_empty():
    assert _normalize_authors("") == []


def test_normalize_authors_single_last_first():
    assert _normalize_authors("Smith, J.") == [{"last": "Smith", "first_initial": "J."}]


def test_normalize_authors_first_last():
    assert _normalize_authors("J. Smith") == [{"last": "Smith", "first_initial": "J."}]


def test_normalize_authors_semicolon_list():
    assert _normalize_authors("Smith, J.; Doe, A.") == [
        {"last": "Smith", "first_initial": "J."},
        {"last": "Doe", "first_initial": "A."},
    ]


def test_normalize_authors_and_separator():
    assert _normalize_authors("Jane Smith and John Doe") == [
        {"last": "Smith", "first_initial": "J."},
        {"last": "Doe", "first_initial": "J."},
    ]


def test_normalize_authors_comma_pair_list():
    # "Last, F., Last, F." pattern
    result = _normalize_authors("Smith, J., Doe, A.")
    assert result == [
        {"last": "Smith", "first_initial": "J."},
        {"last": "Doe", "first_initial": "A."},
    ]


def test_normalize_authors_caps_at_ten():
    authors = "; ".join(f"Author{i}, X." for i in range(15))
    assert len(_normalize_authors(authors)) == 10


# ---------------------------------------------------------------------------
# _extract_json_object
# ---------------------------------------------------------------------------


def test_extract_json_plain():
    raw = '{"sentences": [{"fragment_id": "f1", "text": "S."}]}'
    parsed = _extract_json_object(raw)
    assert parsed == {"sentences": [{"fragment_id": "f1", "text": "S."}]}


def test_extract_json_fenced():
    raw = 'Here is the output:\n```json\n{"sentences": []}\n```\nDone.'
    assert _extract_json_object(raw) == {"sentences": []}


def test_extract_json_with_prose():
    raw = 'Sure, here you go: {"sentences": [{"fragment_id": "x", "text": "y"}]}\nEnd.'
    parsed = _extract_json_object(raw)
    assert parsed == {"sentences": [{"fragment_id": "x", "text": "y"}]}


def test_extract_json_malformed_returns_none():
    assert _extract_json_object("not JSON at all") is None


def test_extract_json_empty_returns_none():
    assert _extract_json_object("") is None


# ---------------------------------------------------------------------------
# generate_sentences
# ---------------------------------------------------------------------------


def _mock_ai(text: str, *, input_tokens: int = 100, output_tokens: int = 50) -> MagicMock:
    ai = MagicMock()
    ai.render_prompt = MagicMock(return_value="RENDERED PROMPT")
    ai.call_with_meta = MagicMock(
        return_value=AICallResult(
            text=text,
            duration_ms=1000,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model="anthropic/claude-sonnet-4-20250514",
        )
    )
    return ai


def test_generate_sentences_empty_fragments():
    ai = _mock_ai("")
    result = generate_sentences(
        [],
        citekey="ck",
        authors="Smith, J.",
        year=2024,
        outline=[],
        language="Russian",
        ai=ai,
    )
    assert result.sentences == {}
    assert result.failed == []
    ai.call_with_meta.assert_not_called()


def test_generate_sentences_all_succeed():
    ai = _mock_ai(
        '{"sentences": ['
        '{"fragment_id": "f1", "text": "S1 [@ck]."},'
        '{"fragment_id": "f2", "text": "S2 [@ck]."}'
        "]}"
    )
    fragments = [
        {"fragment_id": "f1", "text": "orig1", "citation_intent": "background", "assigned_section": "1.1"},
        {"fragment_id": "f2", "text": "orig2", "citation_intent": "method", "assigned_section": "2.1"},
    ]
    result = generate_sentences(
        fragments,
        citekey="ck",
        authors="Smith, J.",
        year=2024,
        outline=[{"section_id": "1.1", "title": "Intro"}],
        language="Russian",
        ai=ai,
    )
    assert result.sentences == {"f1": "S1 [@ck].", "f2": "S2 [@ck]."}
    assert result.failed == []
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.model == "anthropic/claude-sonnet-4-20250514"


def test_generate_sentences_partial_failure():
    """Model returns only 1 of 2 requested fragments — the other goes to failed."""
    ai = _mock_ai('{"sentences": [{"fragment_id": "f1", "text": "ok [@ck]."}]}')
    fragments = [
        {"fragment_id": "f1", "text": "a", "citation_intent": "background", "assigned_section": "1.1"},
        {"fragment_id": "f2", "text": "b", "citation_intent": "background", "assigned_section": "1.1"},
    ]
    result = generate_sentences(
        fragments,
        citekey="ck",
        authors="Smith, J.",
        year=2024,
        outline=[],
        language="Russian",
        ai=ai,
    )
    assert result.sentences == {"f1": "ok [@ck]."}
    assert result.failed == ["f2"]


def test_generate_sentences_malformed_json_all_fail():
    ai = _mock_ai("I cannot help with this.")
    fragments = [
        {"fragment_id": "f1", "text": "a", "citation_intent": "background", "assigned_section": "1.1"},
    ]
    result = generate_sentences(
        fragments,
        citekey="ck",
        authors="Smith, J.",
        year=2024,
        outline=[],
        language="Russian",
        ai=ai,
    )
    assert result.sentences == {}
    assert result.failed == ["f1"]


def test_generate_sentences_ai_returns_none():
    ai = MagicMock()
    ai.render_prompt = MagicMock(return_value="PROMPT")
    ai.call_with_meta = MagicMock(
        return_value=AICallResult(
            text=None,
            duration_ms=1000,
            input_tokens=0,
            output_tokens=0,
            error="timeout",
        )
    )
    fragments = [
        {"fragment_id": "f1", "text": "a", "citation_intent": "background", "assigned_section": "1.1"},
        {"fragment_id": "f2", "text": "b", "citation_intent": "background", "assigned_section": "1.1"},
    ]
    result = generate_sentences(
        fragments,
        citekey="ck",
        authors="Smith, J.",
        year=2024,
        outline=[],
        language="Russian",
        ai=ai,
    )
    assert result.sentences == {}
    assert set(result.failed) == {"f1", "f2"}


def test_generate_sentences_ignores_empty_text():
    ai = _mock_ai(
        '{"sentences": ['
        '{"fragment_id": "f1", "text": ""},'
        '{"fragment_id": "f2", "text": "valid [@ck]."}'
        "]}"
    )
    fragments = [
        {"fragment_id": "f1", "text": "a", "citation_intent": "background", "assigned_section": "1.1"},
        {"fragment_id": "f2", "text": "b", "citation_intent": "background", "assigned_section": "1.1"},
    ]
    result = generate_sentences(
        fragments,
        citekey="ck",
        authors="Smith, J.",
        year=2024,
        outline=[],
        language="Russian",
        ai=ai,
    )
    assert result.sentences == {"f2": "valid [@ck]."}
    assert result.failed == ["f1"]


def test_generate_sentences_prompt_renders_without_jinja_error(tmp_path):
    """Smoke test: the real template renders with realistic inputs."""
    from klemma.ai import AIProviderBase
    from klemma.config import _SHIPPED_PROMPTS_DIR, AIConfig

    class _DummyAI(AIProviderBase):
        def call(self, *a, **kw):
            return None

    ai = _DummyAI(AIConfig())
    prompt_path = _SHIPPED_PROMPTS_DIR / "suggest_sentence.md"
    rendered = ai.render_prompt(
        prompt_path,
        language="Russian",
        citekey="kvanum2024",
        year=2024,
        authors_json='[{"last": "Kvanum", "first_initial": "J."}]',
        outline=[
            {"section_id": "1.1", "title": "Intro", "description": "Motivation"},
            {"section_id": "3.2", "title": "Experiments"},
        ],
        fragments=[
            {
                "fragment_id": "f1",
                "text": "deep learning outperforms dynamical models for 1-3 day lead time",
                "citation_intent": "result_comparison",
                "assigned_section": "3.2",
            },
        ],
    )
    # Key phrases appear
    assert "Russian" in rendered
    assert "kvanum2024" in rendered
    assert "1-3 day" in rendered
    assert "result_comparison" in rendered
