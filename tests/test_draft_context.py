"""Tests for draft context helpers: extract_previous_section_ending, load_outline_context."""

from pathlib import Path

from klemma.skills.context_loader import (
    extract_previous_section_ending,
    load_outline_context,
)

# --- Sample chapter content ---

CHAPTER_CONTENT = """
## 1. Introduction

Introductory text for chapter 1.

### 1.1. Background

This section covers the background.

Some paragraph one.

Some paragraph two about the topic.

### 1.2. Related Work

Related work paragraph one.

Related work closing paragraph here.

### 1.3. Motivation

Motivation text.
""".strip()


class TestExtractPreviousSectionEnding:
    """extract_previous_section_ending() finds last paragraph of preceding section."""

    def test_mid_chapter(self):
        result = extract_previous_section_ending(CHAPTER_CONTENT, "1.3", max_chars=500)
        assert result  # should find 1.2's ending
        assert "closing paragraph" in result

    def test_first_section_of_chapter_returns_empty(self):
        result = extract_previous_section_ending(CHAPTER_CONTENT, "1.1", max_chars=500)
        assert result == ""  # 1.0 doesn't exist → no previous in same chapter

    def test_max_chars_respected(self):
        result = extract_previous_section_ending(CHAPTER_CONTENT, "1.3", max_chars=10)
        assert len(result) <= 10

    def test_empty_content(self):
        result = extract_previous_section_ending("", "1.2")
        assert result == ""

    def test_empty_section_id(self):
        result = extract_previous_section_ending(CHAPTER_CONTENT, "")
        assert result == ""

    def test_non_numeric_section(self):
        result = extract_previous_section_ending(CHAPTER_CONTENT, "introduction")
        assert result == ""

    def test_cross_chapter_no_prev_draft(self):
        """Section 2.1 with no chapter 1 content — returns empty gracefully."""
        result = extract_previous_section_ending("## 2.1. Methods\n\nSome text.", "2.1")
        assert result == ""


# --- Sample outline text ---

OUTLINE_TEXT = """
*Generated: 2026-03-08 10:00*

## 1. Introduction

Overview of chapter 1 covering the problem and motivation.

### 1.1. Background and Context

This section establishes the theoretical foundations of the domain.

### 1.2. Problem Statement

Precise formulation of the research problem.

## 2. Related Work

Survey of existing approaches.

### 2.1. Classic Methods

Historical methods from the 1990s.

### 2.2. Modern Approaches

Current state-of-the-art approaches.

## Scientific Contributions
- nr1: Framework for validation
- nr2: Empirical study
""".strip()

KLEMMA_MD_WITH_OUTLINE = f"""---
type: paper
title: "Test Paper"
description: "A test paper"
scientific_results:
  nr1: Framework for validation
  nr2: Empirical study
---
# Project Context

Description here.

## Outline

{OUTLINE_TEXT}

## Notes

User notes here.
"""


class TestLoadOutlineContext:
    """load_outline_context() reads structured outline context for drafts."""

    def test_section_title_from_klemma_md(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        (tmp_path / "KLEMMA.md").write_text(KLEMMA_MD_WITH_OUTLINE, encoding="utf-8")

        ctx = load_outline_context("1.1", tmp_path)
        assert ctx["section_title"] == "Background and Context"

    def test_section_desc_extracted(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        (tmp_path / "KLEMMA.md").write_text(KLEMMA_MD_WITH_OUTLINE, encoding="utf-8")

        ctx = load_outline_context("1.1", tmp_path)
        assert "theoretical foundations" in ctx["current_section_desc"]

    def test_chapter_desc_extracted(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        (tmp_path / "KLEMMA.md").write_text(KLEMMA_MD_WITH_OUTLINE, encoding="utf-8")

        ctx = load_outline_context("1.2", tmp_path)
        assert "problem" in ctx["current_chapter_desc"].lower() or ctx["current_chapter_desc"]

    def test_scientific_contributions_from_frontmatter(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        (tmp_path / "KLEMMA.md").write_text(KLEMMA_MD_WITH_OUTLINE, encoding="utf-8")

        ctx = load_outline_context("1.1", tmp_path)
        assert "NR1" in ctx["scientific_contributions"]
        assert "Framework" in ctx["scientific_contributions"]

    def test_title_from_frontmatter(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        (tmp_path / "KLEMMA.md").write_text(KLEMMA_MD_WITH_OUTLINE, encoding="utf-8")

        ctx = load_outline_context("2.1", tmp_path)
        assert ctx["title"] == "Test Paper"

    def test_fallback_to_outline_file(self, tmp_path):
        """Falls back to Outline_*.md if KLEMMA.md has no ## Outline section."""
        (tmp_path / ".klemma").mkdir()
        (tmp_path / "KLEMMA.md").write_text("---\ntitle: T\n---\n# Body\n", encoding="utf-8")
        (tmp_path / "Outline_MyProject.md").write_text(OUTLINE_TEXT, encoding="utf-8")

        ctx = load_outline_context("1.1", tmp_path)
        assert ctx["section_title"] == "Background and Context"

    def test_empty_when_no_outline(self, tmp_path):
        (tmp_path / ".klemma").mkdir()
        (tmp_path / "KLEMMA.md").write_text("---\ntitle: T\n---\n# Body\n", encoding="utf-8")

        ctx = load_outline_context("1.1", tmp_path)
        assert ctx["section_title"] == ""
        assert ctx["current_section_desc"] == ""

    def test_missing_project_root(self):
        ctx = load_outline_context("1.1", Path("/nonexistent/path"))
        assert ctx["section_title"] == ""

    def test_empty_section_id(self, tmp_path):
        ctx = load_outline_context("", tmp_path)
        assert ctx == {
            "section_title": "",
            "current_section_desc": "",
            "current_chapter_desc": "",
            "scientific_contributions": "",
            "title": "",
            "description": "",
            "word_target": None,
        }


class TestSectionDraftPromptWithContext:
    """section_draft.md template renders correctly with new context variables."""

    def test_renders_with_outline_context(self):
        from jinja2 import Template  # noqa: I001
        from pathlib import Path

        prompts_dir = Path(__file__).parent.parent / "prompts"
        raw = (prompts_dir / "section_draft.md").read_text(encoding="utf-8")
        t = Template(raw)
        result = t.render(
            dissertation_context="Base context",
            dissertation_context_title="Test Paper",
            section="1.3",
            chapter_num=1,
            chapter_name="Introduction",
            research_report="# Briefing",
            existing_draft="",
            fragments=[],
            source_summaries=[],
            language="ru",
            prev_ending="Previous section ended here.",
            outline_context={
                "description": "A test paper",
                "scientific_contributions": "- NR1: Result one",
                "current_chapter_desc": "Chapter covers motivation",
                "current_section_desc": "Section covers specific topic",
            },
            rag_fragments=[],
            section_title="My Section",
            custom_prompt="",
        )
        assert "Test Paper" in result
        assert "Previous section ended here." in result
        assert "Chapter covers motivation" in result
        assert "NR1: Result one" in result

    def test_renders_without_prev_ending(self):
        from jinja2 import Template  # noqa: I001
        from pathlib import Path

        prompts_dir = Path(__file__).parent.parent / "prompts"
        raw = (prompts_dir / "section_draft.md").read_text(encoding="utf-8")
        t = Template(raw)
        result = t.render(
            dissertation_context="Context",
            dissertation_context_title="",
            section="1.1",
            chapter_num=1,
            chapter_name="",
            research_report="",
            existing_draft="",
            fragments=[],
            source_summaries=[],
            language="ru",
            prev_ending="",
            outline_context={},
            rag_fragments=[],
            section_title="",
            custom_prompt="",
        )
        assert "Окончание предыдущего раздела" not in result
        assert len(result) > 0
