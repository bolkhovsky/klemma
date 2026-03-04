"""Prompt template regression tests (T1-PT).

Validates that all Jinja2 templates render without error and that
ablation-related template features work correctly.
"""

from pathlib import Path

import pytest
from jinja2 import Template

from klemma.evaluation.pipeline import AblationParams, compute_prompt_hash

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Minimal context per template — just enough to avoid undefined variable errors.
# Each key is a prompt filename, value is the render kwargs.
TEMPLATE_CONTEXTS = {
    "morning.md": {
        "project_type": "dissertation",
        "dissertation_context": "Test context",
        "current_chapter": "3",
        "chapter_name": "Methods",
        "current_deadline": "2026-06-01",
        "days_until_deadline": 90,
        "days_without_progress": 0,
        "streak": 5,
        "yesterday_plan": None,
        "chapter_plan": "",
        "library_summary": "",
        "coverage": {"chapters": {"3": 5}},
        "min_sources": 10,
        "gaps": [],
        "fragment_stats": {"total": 100, "by_chapter": {"3": 50}},
        "next_reading": None,
        "writing_constraints": "",
        "language": "ru",
    },
    "extract.md": {
        "title": "Test Paper",
        "authors": "Smith, J.",
        "year": "2024",
        "journal": "Nature",
        "doi": "10.1234/test",
        "abstract": "An abstract",
        "pdf_text": "Paper text...",
        "dissertation_context": "Context",
        "available_tags": ["NLP", "ML"],
    },
    "annotate.md": {
        "title": "Test Paper",
        "authors": "Smith, J.",
        "year": "2024",
        "journal": "Nature",
        "abstract": "Abstract",
        "pdf_text": "Text",
        "library_context": "",
        "dissertation_context": "",
        "available_tags": [],
    },
    "research.md": {
        "project_type": "dissertation",
        "dissertation_context": "Context",
        "target_section": "3.2",
        "chapter_num": 3,
        "chapter_name": "Methods",
        "section_text": "Section not written yet.",
        "full_chapter_draft": "",
        "chapter_plan": "",
        "fragments": "[]",
        "source_summaries": "[]",
        "coverage": {"chapters": {"3": 5}},
        "min_sources": 10,
        "gaps": [],
        "fragment_stats": {"total": 100, "by_chapter": {"3": 50}},
        "language": "ru",
    },
    "research_incremental.md": {
        "project_type": "dissertation",
        "dissertation_context": "Context",
        "target_section": "3.2",
        "chapter_num": 3,
        "chapter_name": "Methods",
        "previous_date": "2026-01-01",
        "previous_text": "",
        "new_citekeys": [],
        "previous_fragment_count": 50,
        "current_fragment_count": 75,
        "user_notes": "",
        "section_text": "Section not written yet.",
        "full_chapter_draft": "",
        "fragments": "[]",
        "source_summaries": "[]",
        "coverage": {"chapters": {"3": 5}},
        "min_sources": 10,
        "gaps": [],
        "language": "ru",
    },
    "librarian.md": {
        "project_type": "dissertation",
        "dissertation_context": "Context",
        "current_chapter": 3,
        "chapter_name": "Methods",
        "deadline": "2026-06-01",
        "days_remaining": 90,
        "mode": "status",
        "summary": {
            "total": 10, "completed": 8, "pending": 2, "failed": 0,
            "fragments_total": 100, "avg_quality": 3.5, "avg_fragments": 10.0,
            "zero_sections": [],
        },
        "chapters": {"3": 5},
        "quality_data": {},
        "ref_gaps": [],
        "sources_compact": "",
        "sources_shown": 0,
        "sources_total": 10,
        "sources_omitted": False,
        "language": "ru",
    },
    "librarian_prune.md": {
        "source_count": 10,
        "sources_compact": [],
    },
    "agent.md": {
        "parent_context": "",
        "project_type": "dissertation",
        "project_context": "Context",
        "chapters_label": "chapters",
        "chapters": {"3": "Methods"},
        "chapters_label_singular": "chapter",
        "scientific_results": {},
        "priority_terms": [],
        "current_section": "3.2",
        "current_chapter": "3",
        "chapter_name": "Methods",
        "current_deadline": "2026-06-01",
        "days_until_deadline": 90,
        "coverage": {"chapters": {"3": 5}},
        "min_sources": 10,
        "gaps": [],
        "fragment_stats": {"total": 100, "by_chapter": {"3": 50}},
        "sources": [],
        "today_plan": None,
        "next_reading": None,
        "project_root": "/tmp",
        "vault_path": "/tmp/vault",
        "outline_content": "",
        "report_index": [],
        "project_file_list": [],
        "today": "2026-02-26",
        "language": "ru",
    },
    "outline.md": {
        "project_type": "dissertation",
        "dissertation_context": "Context",
        "project_files": [],
        "library_summary": "",
        "custom_prompt": "",
        "language": "ru",
    },
    "outline_incremental.md": {
        "project_type": "dissertation",
        "dissertation_context": "Context",
        "project_files": [],
        "library_summary": "",
        "previous_outline": "",
        "user_notes": "",
        "previous_date": "2026-01-01",
        "custom_prompt": "",
        "language": "ru",
    },
    "analyst.md": {
        "pdf_text": "Paper text...",
        "library_entries": "- smith2020: Some Paper",
        "paper_citekey": "jones2024",
        "paper_title": "Test Paper",
    },
    "introduction_draft.md": {
        "dissertation_context": "Test context",
        "chapters": {1: "Chapter 1", 2: "Chapter 2"},
        "scientific_results": {"nr1": "Result 1"},
        "fragments_by_type": {},
        "ref_gaps": [],
        "author_publications": "",
        "target_section": "",
    },
    "reconstruct.md": {
        "paper_title": "Test Paper",
        "abstract": "An abstract",
        "keywords": ["NLP", "citations"],
        "sections": [
            {"section_id": "1", "title": "Introduction", "description": "Background"},
            {"section_id": "2", "title": "Methods", "description": "Our approach"},
        ],
        "sources": [
            {
                "citekey": "smith2020",
                "title": "Smith et al.",
                "year": "2020",
                "abstract": "A paper about things",
                "fragments": [
                    {"intent": "background", "text": "This is a background fragment"},
                    {"intent": "method", "text": "This is a method fragment"},
                ],
            },
        ],
    },
}


class TestAllTemplatesRender:
    """Every shipped prompt template must render without Jinja2 errors."""

    @pytest.mark.parametrize("template_name", sorted(TEMPLATE_CONTEXTS.keys()))
    def test_template_renders(self, template_name):
        path = PROMPTS_DIR / template_name
        assert path.exists(), f"Template {template_name} not found in {PROMPTS_DIR}"

        raw = path.read_text(encoding="utf-8")
        t = Template(raw)
        result = t.render(**TEMPLATE_CONTEXTS[template_name])

        assert len(result) > 0, f"Template {template_name} rendered empty"

    def test_all_shipped_templates_have_contexts(self):
        """Ensure we have test contexts for every .md file (except CLAUDE.md)."""
        shipped = {
            p.name
            for p in PROMPTS_DIR.glob("*.md")
            if p.name != "CLAUDE.md"
        }
        tested = set(TEMPLATE_CONTEXTS.keys())
        missing = shipped - tested
        assert not missing, f"Templates without test contexts: {missing}"


class TestReconstructAblationVariants:
    """Reconstruct prompt renders correctly with ablation variables."""

    def _render(self, **extra):
        raw = (PROMPTS_DIR / "reconstruct.md").read_text(encoding="utf-8")
        ctx = {**TEMPLATE_CONTEXTS["reconstruct.md"], **extra}
        return Template(raw).render(**ctx)

    def test_default_no_max_recs(self):
        result = self._render()
        assert "Be thorough" in result
        assert "at most" not in result

    def test_max_recs_per_section(self):
        result = self._render(max_recs_per_section=3)
        assert "at most 3 sources per section" in result
        assert "Be thorough" not in result

    def test_no_examples_by_default(self):
        result = self._render()
        assert "## Examples" not in result

    def test_fewshot_examples(self):
        examples = [
            {
                "section_id": "2.1",
                "section_title": "Related Work",
                "citekey": "jones2021",
                "intent": "background",
                "justification": "Provides survey of the field",
            },
        ]
        result = self._render(examples=examples)
        assert "## Examples" in result
        assert "jones2021" in result
        assert "Related Work" in result

    def test_fewshot_and_max_recs_together(self):
        examples = [
            {
                "section_id": "1",
                "section_title": "Intro",
                "citekey": "test2020",
                "intent": "method",
                "justification": "Used their algorithm",
            },
        ]
        result = self._render(max_recs_per_section=5, examples=examples)
        assert "at most 5 sources" in result
        assert "## Examples" in result
        assert "test2020" in result


class TestPromptHash:
    """Prompt hash is deterministic and changes when content changes."""

    def test_hash_deterministic(self):
        h1 = compute_prompt_hash("reconstruct.md")
        h2 = compute_prompt_hash("reconstruct.md")
        assert h1 == h2
        assert len(h1) == 12

    def test_hash_different_templates(self):
        h1 = compute_prompt_hash("reconstruct.md")
        h2 = compute_prompt_hash("analyst.md")
        assert h1 != h2

    def test_hash_missing_template(self):
        h = compute_prompt_hash("nonexistent_template.md")
        assert h == ""


class TestAblationParams:
    """AblationParams model defaults and factory methods."""

    def test_defaults_match_current_behavior(self):
        p = AblationParams()
        assert p.temperature == 0.2
        assert p.max_recs_per_section is None
        assert p.fragments_per_source == 5
        assert p.prompt_variant == "default"
        assert p.examples == []

    def test_to_snapshot(self):
        p = AblationParams(temperature=0.7, max_recs_per_section=3)
        snap = p.to_snapshot()
        assert snap["temperature"] == 0.7
        assert snap["max_recs_per_section"] == 3
        assert snap["fragments_per_source"] == 5
        assert snap["prompt_variant"] == "default"

    def test_with_fewshot_factory(self):
        p = AblationParams.with_fewshot(temperature=0.5)
        assert p.prompt_variant == "fewshot"
        assert p.temperature == 0.5
        assert len(p.examples) == 2
        assert p.examples[0]["citekey"] == "cohan2019"

    def test_with_fewshot_and_max_recs(self):
        p = AblationParams.with_fewshot(max_recs_per_section=3)
        assert p.prompt_variant == "fewshot"
        assert p.max_recs_per_section == 3
        assert len(p.examples) > 0
