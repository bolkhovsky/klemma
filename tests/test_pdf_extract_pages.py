"""Tests for `PDFExtractor.extract_pages` — the unbounded per-page extractor
consumed by the raw sidecar writer and, later, the citation-drift checker.

The signature `(pdf_path: Path) -> list[str]` is part of the public API that
downstream tooling relies on, so breakage here is a contract violation.
"""

from __future__ import annotations

from pathlib import Path

import fitz

from klemma.literature.pdf import PDFExtractor


def _write_pdf(path: Path, pages: list[str]) -> None:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
    doc.save(str(path))
    doc.close()


def test_extract_pages_returns_one_per_page(tmp_path: Path) -> None:
    pdf = tmp_path / "three_pages.pdf"
    _write_pdf(
        pdf,
        [
            "Introduction page with first paragraph.",
            "Methods page describing experimental setup.",
            "Results page with conclusions.",
        ],
    )

    extractor = PDFExtractor()
    pages = extractor.extract_pages(pdf)

    assert len(pages) == 3
    assert "Introduction" in pages[0]
    assert "Methods" in pages[1]
    assert "Results" in pages[2]
    # No [Page N] inline markers — those belong to `extract()`, not this API.
    for text in pages:
        assert "[Page" not in text


def test_extract_pages_missing_file_returns_empty() -> None:
    extractor = PDFExtractor()
    assert extractor.extract_pages(Path("/nonexistent/doc.pdf")) == []


def test_extract_pages_no_truncation(tmp_path: Path) -> None:
    """extract() truncates to max_chars; extract_pages() must not."""
    pdf = tmp_path / "long.pdf"

    # Build a single page with substantially more characters than max_chars.
    long_line = "alpha beta gamma delta epsilon " * 50  # ~1500 chars per line
    body = "\n".join(long_line for _ in range(30))  # ~45000 chars before wrapping
    _write_pdf(pdf, [body])

    extractor = PDFExtractor(max_chars=100)  # absurdly small cap
    pages = extractor.extract_pages(pdf)

    assert len(pages) == 1
    # The per-page return is the full cleaned text, not the 100-char cap
    assert len(pages[0]) > 1000
    assert "content truncated" not in pages[0]


def test_extract_pages_empty_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "empty.pdf"
    doc = fitz.open()
    doc.new_page()  # a single empty page
    doc.save(str(pdf))
    doc.close()

    extractor = PDFExtractor()
    pages = extractor.extract_pages(pdf)

    assert len(pages) == 1
    assert pages[0] == ""


def test_extract_pages_signature_is_list_of_str(tmp_path: Path) -> None:
    """Lock in the downstream contract: return is always a list[str]."""
    pdf = tmp_path / "one.pdf"
    _write_pdf(pdf, ["single"])

    result = PDFExtractor().extract_pages(pdf)
    assert isinstance(result, list)
    assert all(isinstance(p, str) for p in result)
