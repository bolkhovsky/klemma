"""Tests for draft placeholder and word-target fixes (issue #133).

TDD: these tests define expected behaviour BEFORE the implementation.

Two sub-issues:
1. Table cells requiring experimental data must use [DATA] / [from results]
   placeholders instead of fabricated numbers.
2. Section word target from KLEMMA.md outline must be passed to the
   rendered prompt, replacing the hardcoded '800–2000 слов' range.
"""

from pathlib import Path

import pytest
from jinja2 import Template

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _render(section_draft_md: str, **kwargs) -> str:
    """Render prompts/section_draft.md with the given context variables."""
    defaults = dict(
        dissertation_context="Test dissertation",
        dissertation_context_title="",
        section="3.1",
        chapter_num=3,
        chapter_name="Эксперименты",
        section_title="Результаты эксперимента",
        research_report="",
        existing_draft="",
        fragments=[],
        rag_fragments=[],
        source_summaries=[],
        language="ru",
        prev_ending="",
        outline_context={},
        custom_prompt="",
    )
    defaults.update(kwargs)
    t = Template(section_draft_md)
    return t.render(**defaults)


@pytest.fixture(scope="module")
def section_draft_md() -> str:
    return (PROMPTS_DIR / "section_draft.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Sub-issue 1: No-fabrication instruction for tables
# ---------------------------------------------------------------------------


class TestNoFabricationInstruction:
    """Prompt must explicitly forbid fabricating numerical data in tables."""

    def test_prompt_contains_placeholder_instruction(self, section_draft_md):
        """Rendered prompt must contain a [DATA] or placeholder instruction."""
        rendered = _render(section_draft_md)
        # The prompt must mention [DATA] placeholder or equivalent instruction
        assert "[DATA]" in rendered or "placeholder" in rendered.lower() or "[from results]" in rendered.lower()

    def test_prompt_forbids_inventing_numbers(self, section_draft_md):
        """Rendered prompt must instruct the model NOT to invent numerical values."""
        rendered = _render(section_draft_md)
        rendered_lower = rendered.lower()
        # Should contain some form of prohibition on fabricating data
        assert any(
            phrase in rendered_lower
            for phrase in [
                "не придумывай",
                "не изобретай",
                "не фабрикуй",
                "не генерируй числа",
                "do not fabricate",
                "do not invent",
            ]
        ), "Expected fabrication-prohibition phrase in prompt, but none found."

    def test_placeholder_instruction_present_for_experimental_sections(
        self, section_draft_md
    ):
        """For a section mentioning 'results' or 'table', [DATA] marker must appear."""
        rendered = _render(
            section_draft_md,
            section="3.1",
            section_title="Таблица результатов эксперимента",
            outline_context={
                "current_section_desc": (
                    "Представить таблицу с результатами: RMSE, IIEE по датам. "
                    "Сравнить метод A и метод B."
                ),
            },
        )
        assert "[DATA]" in rendered or "[from results]" in rendered.lower()

    def test_todo_comment_for_tables(self, section_draft_md):
        """Rendered prompt must include a TODO fill-from-results instruction."""
        rendered = _render(section_draft_md)
        assert "TODO" in rendered or "RESULTS.md" in rendered or "результат" in rendered.lower()


# ---------------------------------------------------------------------------
# Sub-issue 2: Word target from outline context
# ---------------------------------------------------------------------------


class TestWordTargetFromOutline:
    """Word target from outline_context.word_target overrides hardcoded 800-2000."""

    def test_word_target_present_in_rendered_prompt(self, section_draft_md):
        """When outline_context contains word_target, value appears in prompt."""
        rendered = _render(
            section_draft_md,
            outline_context={"word_target": 200},
        )
        assert "200" in rendered

    def test_word_target_replaces_hardcoded_range(self, section_draft_md):
        """When word_target is set, hardcoded 800–2000 range must NOT appear."""
        rendered = _render(
            section_draft_md,
            outline_context={"word_target": 200},
        )
        # Neither the en-dash variant nor the hyphen variant should remain
        assert "800" not in rendered, (
            "Hardcoded 800 still present even though word_target=200 was provided"
        )
        assert "2000" not in rendered, (
            "Hardcoded 2000 still present even though word_target=200 was provided"
        )

    def test_hardcoded_range_used_when_no_word_target(self, section_draft_md):
        """Without word_target, the fallback 800–2000 must still appear."""
        rendered = _render(
            section_draft_md,
            outline_context={},
        )
        # The fallback should keep the original range
        assert "800" in rendered or "2000" in rendered

    def test_word_target_none_uses_fallback(self, section_draft_md):
        """word_target=None is treated as absent → fallback range applies."""
        rendered = _render(
            section_draft_md,
            outline_context={"word_target": None},
        )
        assert "800" in rendered or "2000" in rendered

    def test_word_target_large_value(self, section_draft_md):
        """word_target=1500 appears in rendered prompt."""
        rendered = _render(
            section_draft_md,
            outline_context={"word_target": 1500},
        )
        assert "1500" in rendered


# ---------------------------------------------------------------------------
# Sub-issue 2b: word_target parsing in context_loader
# ---------------------------------------------------------------------------


class TestParseWordTargetFromOutline:
    """load_outline_context() must parse word_target from section descriptions."""

    def _run(self, outline_body: str, section: str = "3.1") -> dict:
        """Helper: write a temp KLEMMA.md and call load_outline_context."""
        import tempfile

        from klemma.skills.context_loader import load_outline_context

        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            klemma_md = project_root / "KLEMMA.md"
            klemma_md.write_text(
                f"---\ntitle: Test\n---\n{outline_body}",
                encoding="utf-8",
            )
            return load_outline_context(section, project_root)

    def test_parses_tilde_slova_format(self):
        """*~200 слов* in section desc → word_target=200."""
        body = (
            "## Outline\n"
            "### 3.1. Эксперименты\n"
            "Описание раздела.\n"
            "*~200 слов*\n"
        )
        ctx = self._run(body)
        assert ctx.get("word_target") == 200

    def test_parses_approx_words_format(self):
        """(~300 words) in section desc → word_target=300."""
        body = (
            "## Outline\n"
            "### 3.1. Results\n"
            "Section description. (~300 words)\n"
        )
        ctx = self._run(body)
        assert ctx.get("word_target") == 300

    def test_parses_approximately_sign(self):
        """≈400 слов in section desc → word_target=400."""
        body = (
            "## Outline\n"
            "### 3.1. Методы\n"
            "Подробное описание. ≈400 слов\n"
        )
        ctx = self._run(body)
        assert ctx.get("word_target") == 400

    def test_no_word_target_returns_none(self):
        """Section without word count hint → word_target is None or absent."""
        body = (
            "## Outline\n"
            "### 3.1. Методы\n"
            "Описание без указания объёма.\n"
        )
        ctx = self._run(body)
        assert ctx.get("word_target") is None

    def test_word_target_not_inherited_from_other_sections(self):
        """word_target for 3.1 must not bleed from 3.2 word hint."""
        body = (
            "## Outline\n"
            "### 3.1. Методы\n"
            "Раздел без объёма.\n"
            "### 3.2. Результаты\n"
            "Таблицы.\n"
            "*~500 слов*\n"
        )
        ctx = self._run(body, section="3.1")
        assert ctx.get("word_target") is None
