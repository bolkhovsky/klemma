"""Tests for draft_parser.py (#76 CiteQ onboarding)."""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from klemma.literature.draft_parser import (
    DetectedSection,
    DraftParseResult,
    _is_bib_marker,
    parse_draft_pdf,
)


def _create_test_pdf(path: Path, text_pages: list[str], title_size: float = 18.0) -> None:
    """Create a minimal PDF with text pages for testing."""
    doc = fitz.open()
    for i, text in enumerate(text_pages):
        page = doc.new_page()
        if i == 0:
            # First page: title in large font
            first_line = text.split("\n")[0]
            rest = "\n".join(text.split("\n")[1:])
            page.insert_text((72, 80), first_line, fontsize=title_size)
            page.insert_text((72, 120), rest, fontsize=11)
        else:
            page.insert_text((72, 72), text, fontsize=11)
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------------------
# Basic parsing
# ---------------------------------------------------------------------------


def test_parse_empty_pdf(tmp_path):
    pdf = tmp_path / "empty.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(pdf))
    doc.close()

    result = parse_draft_pdf(pdf)
    assert result.page_count == 1
    assert result.sections == []
    assert result.references == []


def test_parse_nonexistent_file():
    result = parse_draft_pdf("/nonexistent/file.pdf")
    assert result.page_count == 0
    assert result.title == ""


def test_parse_pdf_with_sections(tmp_path):
    pdf = tmp_path / "draft.pdf"
    _create_test_pdf(pdf, [
        "My Research Paper\n\n"
        "1. Introduction\n"
        "This is the introduction text about the topic.\n\n"
        "2. Methods\n"
        "We used machine learning methods.\n\n"
        "2.1 Data Collection\n"
        "Data was collected from multiple sources.\n\n"
        "3. Results\n"
        "The results show improvement.\n"
    ])

    result = parse_draft_pdf(pdf)
    assert result.page_count == 1
    assert len(result.sections) >= 3
    assert any("Introduction" in s.heading for s in result.sections)
    assert any("Methods" in s.heading for s in result.sections)
    assert any("Results" in s.heading for s in result.sections)


def test_parse_pdf_section_levels(tmp_path):
    pdf = tmp_path / "levels.pdf"
    _create_test_pdf(pdf, [
        "Title\n\n"
        "1. Chapter One\n"
        "Text.\n\n"
        "1.1 Section One\n"
        "More text.\n\n"
        "1.1.1 Subsection\n"
        "Even more text.\n"
    ])

    result = parse_draft_pdf(pdf)
    levels = {s.heading.split()[0]: s.level for s in result.sections}
    assert levels.get("1") == 1 or levels.get("1.") == 1
    if "1.1" in levels:
        assert levels["1.1"] == 2
    if "1.1.1" in levels:
        assert levels["1.1.1"] == 3


def test_parse_pdf_with_bibliography(tmp_path):
    pdf = tmp_path / "withbib.pdf"
    _create_test_pdf(pdf, [
        "Research Paper Title\n\n"
        "1. Introduction\n"
        "Some introduction text.\n\n"
        "References\n"
        "[1] Smith, J. (2020). Machine Learning Approaches. Nature, 123, 45-67.\n"
        "[2] Jones, K. (2019). Deep Learning in NLP. Science, 456, 89-101.\n"
    ])

    result = parse_draft_pdf(pdf)
    assert len(result.references) >= 1
    years = [r.year for r in result.references if r.year]
    assert 2020 in years or 2019 in years


def test_parse_pdf_title_extraction(tmp_path):
    pdf = tmp_path / "titled.pdf"
    _create_test_pdf(pdf, [
        "My Important Research Paper\nAuthor Name\nAbstract text here."
    ], title_size=20.0)

    result = parse_draft_pdf(pdf)
    assert "Important" in result.title or "Research" in result.title


# ---------------------------------------------------------------------------
# Bibliography markers
# ---------------------------------------------------------------------------


def test_bib_marker_english():
    assert _is_bib_marker("References")
    assert _is_bib_marker("Bibliography")
    assert _is_bib_marker("Works Cited")


def test_bib_marker_russian():
    assert _is_bib_marker("Литература")
    assert _is_bib_marker("Список литературы")
    assert _is_bib_marker("Список использованных источников")


def test_bib_marker_with_number():
    assert _is_bib_marker("5. References")
    assert _is_bib_marker("4.1 Литература")


def test_not_bib_marker():
    assert not _is_bib_marker("Introduction")
    assert not _is_bib_marker("Methods and References to prior work")
    assert not _is_bib_marker("")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


def test_draft_parse_result_defaults():
    r = DraftParseResult()
    assert r.title == ""
    assert r.sections == []
    assert r.references == []
    assert r.page_count == 0


def test_detected_section_defaults():
    s = DetectedSection(heading="1. Intro", level=1)
    assert s.text == ""
    assert s.page_start == 0
