"""Tests for section draft generation (drafter.py)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from jinja2 import Template

from klemma.skills.drafter import (
    DraftResult,
    _extract_citations,
    _filter_hallucinated_citations,
    generate_draft,
)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class TestDraftResultDataclass:
    """DraftResult fields and defaults."""

    def test_defaults(self):
        r = DraftResult()
        assert r.section == ""
        assert r.chapter == 0
        assert r.text == ""
        assert r.word_count == 0
        assert r.citations_used == []
        assert r.filtered_citekeys == []
        assert r.research_report_used is False

    def test_with_values(self):
        r = DraftResult(
            section="1.3.2",
            chapter=1,
            text="Some text",
            word_count=2,
            citations_used=["smith2020"],
            research_report_used=True,
        )
        assert r.section == "1.3.2"
        assert r.citations_used == ["smith2020"]
        assert r.research_report_used is True


class TestExtractCitations:
    """Regex [@citekey] parsing."""

    def test_single_citation(self):
        assert _extract_citations("text [@smith2020] more") == ["smith2020"]

    def test_multiple_citations(self):
        text = "[@aaa] and [@bbb-2021] also [@ccc]"
        assert _extract_citations(text) == ["aaa", "bbb-2021", "ccc"]

    def test_no_citations(self):
        assert _extract_citations("plain text without citations") == []

    def test_repeated_citations(self):
        text = "[@a] and [@a] again"
        assert _extract_citations(text) == ["a", "a"]

    def test_inline_in_sentence(self):
        text = "As shown by [@jones2019], the method works."
        assert _extract_citations(text) == ["jones2019"]


class TestFilterHallucinatedCitations:
    """Remove invalid citekeys, keep valid ones."""

    def test_keeps_valid(self):
        text = "Text [@valid1] and [@valid2]."
        cleaned, removed = _filter_hallucinated_citations(
            text, {"valid1", "valid2"},
        )
        assert "[@valid1]" in cleaned
        assert "[@valid2]" in cleaned
        assert removed == []

    def test_removes_invalid(self):
        text = "Text [@valid] and [@fake]."
        cleaned, removed = _filter_hallucinated_citations(
            text, {"valid"},
        )
        assert "[@valid]" in cleaned
        assert "[@fake]" not in cleaned
        assert removed == ["fake"]

    def test_empty_valid_set(self):
        text = "[@a] [@b]"
        cleaned, removed = _filter_hallucinated_citations(text, set())
        assert "[@" not in cleaned
        assert sorted(removed) == ["a", "b"]

    def test_no_citations_in_text(self):
        text = "Plain text"
        cleaned, removed = _filter_hallucinated_citations(text, {"any"})
        assert cleaned == "Plain text"
        assert removed == []

    def test_deduplicates_removed(self):
        text = "[@fake] and [@fake] again"
        cleaned, removed = _filter_hallucinated_citations(text, set())
        assert removed == ["fake"]


class TestGenerateDraft:
    """Full pipeline: AI call + citation extraction + filtering."""

    @pytest.fixture
    def config(self):
        cfg = MagicMock()
        cfg.ai.language = "ru"
        cfg.ai.task_classes = {}
        cfg.ai.class_model_map = {}
        cfg.dissertation.chapters = {1: "Введение"}
        return cfg

    @pytest.fixture
    def ai(self):
        mock_ai = MagicMock()
        mock_ai.render_prompt.return_value = "rendered prompt"
        return mock_ai

    def _call(self, config, ai, **kwargs):
        """Helper: call generate_draft with resolve_prompt + resolve_task_model patched."""
        with (
            patch("klemma.skills.drafter.resolve_task_model", return_value=None),
            patch("klemma.skills.drafter.resolve_prompt", return_value=Path("/fake/prompt.md")),
        ):
            return generate_draft(**kwargs, config=config, ai=ai)

    def test_with_research_report(self, config, ai):
        ai.call.return_value = "Текст раздела [@smith2020] с цитатой."

        result = self._call(
            config, ai,
            section="1.3", chapter=1,
            research_report_content="# Research briefing",
            valid_citekeys={"smith2020"},
        )

        assert result.text
        assert result.word_count > 0
        assert "smith2020" in result.citations_used
        assert result.research_report_used is True
        assert result.filtered_citekeys == []

    def test_without_research_report(self, config, ai):
        ai.call.return_value = "Краткий текст."

        result = self._call(config, ai, section="2.1", chapter=2)

        assert result.text == "Краткий текст."
        assert result.research_report_used is False

    def test_filters_hallucinated_citekeys(self, config, ai):
        ai.call.return_value = "Text [@real] and [@hallucinated]."

        result = self._call(
            config, ai,
            section="1.1", chapter=1,
            valid_citekeys={"real"},
        )

        assert "real" in result.citations_used
        assert "hallucinated" not in result.citations_used
        assert "hallucinated" in result.filtered_citekeys

    def test_empty_ai_response(self, config, ai):
        ai.call.return_value = ""

        result = self._call(config, ai, section="1.1", chapter=1)

        assert result.text == ""
        assert result.word_count == 0

    def test_no_filtering_without_valid_citekeys(self, config, ai):
        ai.call.return_value = "Text [@any_key]."

        result = self._call(config, ai, section="1.1", chapter=1)

        assert "any_key" in result.citations_used
        assert result.filtered_citekeys == []


class TestPromptRenders:
    """section_draft.md renders without Jinja2 errors."""

    def test_renders_with_full_context(self):
        raw = (PROMPTS_DIR / "section_draft.md").read_text(encoding="utf-8")
        t = Template(raw)
        result = t.render(
            dissertation_context="Test context",
            section="1.3.2",
            chapter_num=1,
            chapter_name="Introduction",
            research_report="# Research briefing\nContent",
            existing_draft="Existing text here",
            fragments=[
                {"source": "smith2020", "text": "Fragment text", "type": "result", "relevance": 4},
            ],
            source_summaries=[
                {"citekey": "smith2020", "quality": 5, "priority": "high", "summary": "Good paper"},
            ],
            language="ru",
        )
        assert len(result) > 0
        assert "1.3.2" in result

    def test_renders_minimal_context(self):
        raw = (PROMPTS_DIR / "section_draft.md").read_text(encoding="utf-8")
        t = Template(raw)
        result = t.render(
            dissertation_context="",
            section="2.1",
            chapter_num=2,
            chapter_name="",
            research_report="",
            existing_draft="",
            fragments=[],
            source_summaries=[],
            language="ru",
        )
        assert len(result) > 0
