"""Tests for introduction draft generation (#86)."""

from pathlib import Path
from unittest.mock import MagicMock

from klemma.skills.introduction_drafter import (
    GOST_SECTIONS,
    IntroductionResult,
    generate_introduction,
)


def test_gost_sections_count():
    """12 mandatory ГОСТ sections."""
    assert len(GOST_SECTIONS) == 12
    assert "актуальность" in GOST_SECTIONS
    assert "публикации" in GOST_SECTIONS


def test_generate_introduction_all_sections(tmp_path):
    """generate_introduction calls AI and returns result."""
    from klemma.state import StateManager

    state = StateManager(str(tmp_path / "test.db"))
    ai = MagicMock()
    ai.call.return_value = "## 1. Актуальность\n\nТекст.\n\n## 2. Цель\n\nТекст."
    ai.render_prompt.return_value = "rendered prompt"

    config = MagicMock()
    config.ai._resolved_api_keys = {}

    result = generate_introduction(
        config, state, ai,
        dissertation_context="Test context",
        klemma_home=tmp_path,
    )

    assert isinstance(result, IntroductionResult)
    assert "Актуальность" in result.text
    assert result.section_count == 2
    ai.render_prompt.assert_called_once()
    ai.call.assert_called_once()


def test_generate_introduction_single_section(tmp_path):
    """generate_introduction with target_section passes it to prompt."""
    from klemma.state import StateManager

    state = StateManager(str(tmp_path / "test.db"))
    ai = MagicMock()
    ai.call.return_value = "## 1. Актуальность\n\nТекст актуальности."
    ai.render_prompt.return_value = "rendered"

    config = MagicMock()
    config.ai._resolved_api_keys = {}

    result = generate_introduction(
        config, state, ai,
        dissertation_context="Test",
        klemma_home=tmp_path,
        target_section="актуальность",
    )

    # Verify target_section was passed to render_prompt
    call_kwargs = ai.render_prompt.call_args
    assert call_kwargs[1]["target_section"] == "актуальность"
    assert result.section_count == 1


def test_generate_introduction_with_author_publications(tmp_path):
    """Author publications phrase is included when source_role data exists."""
    from klemma.state import StateManager

    state = StateManager(str(tmp_path / "test.db"))
    state.register_sources(["@a", "@b"])
    state.set_source_role("@a", "author_vak")
    state.set_source_role("@b", "author_conf")

    ai = MagicMock()
    ai.call.return_value = "## 11. Публикации\n\nТекст."
    ai.render_prompt.return_value = "rendered"

    config = MagicMock()
    config.ai._resolved_api_keys = {}

    generate_introduction(
        config, state, ai,
        dissertation_context="Test",
        klemma_home=tmp_path,
    )

    call_kwargs = ai.render_prompt.call_args
    assert "печатных изданиях" in call_kwargs[1]["author_publications"]


def test_introduction_result_dataclass():
    """IntroductionResult has expected fields."""
    r = IntroductionResult()
    assert r.text == ""
    assert r.section_count == 0

    r2 = IntroductionResult(text="content", section_count=5)
    assert r2.text == "content"
    assert r2.section_count == 5


def test_prompt_template_renders(tmp_path):
    """introduction_draft.md template renders without Jinja2 errors."""
    from jinja2 import Template

    template_path = Path(__file__).parent.parent / "prompts" / "introduction_draft.md"
    template_text = template_path.read_text(encoding="utf-8")
    t = Template(template_text)

    rendered = t.render(
        dissertation_context="Test dissertation",
        chapters={1: "Chapter 1", 2: "Chapter 2"},
        scientific_results={"nr1": "Result 1"},
        fragments_by_type={"methodology": [{"citekey": "@test", "text": "fragment text"}]},
        ref_gaps=[{"ref_authors": "Smith", "ref_year": "2024", "why_relevant": "important"}],
        author_publications="Основные результаты изложены в 5 печатных изданиях.",
        target_section="",
    )

    assert "Test dissertation" in rendered
    assert "Chapter 1" in rendered
    assert "Result 1" in rendered
