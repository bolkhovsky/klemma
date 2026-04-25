"""Tests for `PDFExtractor.extract_pages` and `build_chunks_from_pages` (M3).

extract_pages() is a stable public API — its signature is a contract.
build_chunks_from_pages() is the M3 chunk builder; tested here alongside.
"""

from __future__ import annotations

from pathlib import Path

import fitz

from klemma.literature.pdf import ChunkRecord, PDFExtractor, build_chunks_from_pages


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


# ---------------------------------------------------------------------------
# build_chunks_from_pages
# ---------------------------------------------------------------------------


def _make_pages(n: int, chars_per_page: int = 1000) -> list[str]:
    return [f"Content for page {i + 1}. " * (chars_per_page // 24) for i in range(n)]


def test_build_chunks_empty_returns_empty():
    assert build_chunks_from_pages([]) == []


def test_build_chunks_short_text_single_chunk():
    chunks = build_chunks_from_pages(["Hello world."], chunk_size=25_000)
    assert len(chunks) == 1
    c = chunks[0]
    assert isinstance(c, ChunkRecord)
    assert c.index == 0
    assert c.page_start == 1
    assert c.page_end == 1
    assert "[Page 1]" in c.text
    assert c.char_start == 0


def test_build_chunks_page_markers_in_text():
    pages = ["Page one text.", "Page two text.", "Page three text."]
    chunks = build_chunks_from_pages(pages, chunk_size=25_000)
    assert len(chunks) == 1
    assert "[Page 1]" in chunks[0].text
    assert "[Page 2]" in chunks[0].text
    assert "[Page 3]" in chunks[0].text


def test_build_chunks_long_text_multiple_chunks():
    pages = _make_pages(10, chars_per_page=4000)
    chunks = build_chunks_from_pages(pages, chunk_size=10_000, overlap=1_000)
    assert len(chunks) >= 3


def test_build_chunks_sequential_indices():
    pages = _make_pages(8, chars_per_page=5000)
    chunks = build_chunks_from_pages(pages, chunk_size=10_000, overlap=1_000)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_build_chunks_first_starts_at_zero():
    pages = _make_pages(5, chars_per_page=6000)
    chunks = build_chunks_from_pages(pages, chunk_size=10_000, overlap=1_000)
    assert chunks[0].char_start == 0


def test_build_chunks_last_covers_full_text():
    pages = _make_pages(4, chars_per_page=6000)
    chunks = build_chunks_from_pages(pages, chunk_size=10_000, overlap=1_000)
    full = "\n\n".join(f"[Page {i + 1}]\n{p}" for i, p in enumerate(pages))
    assert chunks[-1].char_end == len(full)


def test_build_chunks_max_size_with_slack():
    pages = _make_pages(8, chars_per_page=6000)
    chunks = build_chunks_from_pages(pages, chunk_size=10_000, overlap=1_000)
    for c in chunks:
        assert len(c.text) <= 10_000 + 500, f"chunk {c.index} too large: {len(c.text)}"


def test_build_chunks_page_markers_in_every_chunk():
    pages = _make_pages(6, chars_per_page=5000)
    chunks = build_chunks_from_pages(pages, chunk_size=10_000, overlap=1_000)
    for c in chunks:
        assert "[Page " in c.text, f"chunk {c.index} has no page marker"


def test_build_chunks_page_range_valid():
    pages = _make_pages(5, chars_per_page=6000)
    chunks = build_chunks_from_pages(pages, chunk_size=10_000, overlap=1_000)
    for c in chunks:
        assert 1 <= c.page_start <= len(pages)
        assert c.page_start <= c.page_end <= len(pages)


def test_build_chunks_mid_page_chunk_correct_page_attribution():
    """When a single page exceeds chunk_size, the chunker prepends the active [Page N]
    marker to mid-page chunks so the AI can always ground page numbers.
    page_start must equal the active page at chunk start, not 1 or n_pages.
    """
    # Page 1: short; Page 2: very long (> chunk_size); Page 3: short
    chunk_size = 5_000
    page2_text = "B" * (chunk_size * 3)  # forces multiple mid-page chunks on page 2
    pages = ["Short page 1 content.", page2_text, "Short page 3 content."]
    chunks = build_chunks_from_pages(pages, chunk_size=chunk_size, overlap=500)

    n_pages = len(pages)
    mid_page_chunks_found = 0
    for c in chunks:
        assert 1 <= c.page_start <= n_pages, f"chunk {c.index} page_start out of range"
        assert c.page_start <= c.page_end <= n_pages, f"chunk {c.index} page range invalid"
        # Every chunk must have a [Page N] marker (prepended if mid-page)
        assert "[Page " in c.text, f"chunk {c.index} has no page marker"
        # Mid-page chunks on page 2 must attribute to page 2, not page 1 or n_pages
        if c.text.startswith("[Page 2]") and "B" * 100 in c.text:
            mid_page_chunks_found += 1
            assert c.page_start == 2, (
                f"mid-page chunk {c.index} should start on page 2, got {c.page_start}"
            )

    assert mid_page_chunks_found >= 2, "expected multiple mid-page chunks on page 2"


# ---------------------------------------------------------------------------
# Overlap dedup helper
# ---------------------------------------------------------------------------


def test_dedup_by_prefix_removes_near_duplicates():
    from klemma.api.tasks import dedup_fragments_by_prefix as _dedup_fragments_by_prefix
    from klemma.models import FragmentRecord

    def _frag(fid: str, text: str) -> FragmentRecord:
        return FragmentRecord(
            fragment_id=fid, paper_id="p1", fragment_text=text,
            fragment_type="key_idea", page_number=1,
            citation_intent=None, content_hash=fid,
        )

    long_a = "A" * 120 + " distinct ending one."
    long_b = "A" * 120 + " slightly different ending."
    long_c = "B" * 120 + " completely different."

    result = _dedup_fragments_by_prefix(
        [_frag("a", long_a), _frag("b", long_b), _frag("c", long_c)],
        min_prefix=100,
    )
    assert len(result) == 2
    assert result[0].fragment_id == "a"
    assert result[1].fragment_id == "c"


def test_dedup_keeps_short_fragments():
    from klemma.api.tasks import dedup_fragments_by_prefix as _dedup_fragments_by_prefix
    from klemma.models import FragmentRecord

    short = FragmentRecord(
        fragment_id="s1", paper_id="p1", fragment_text="Short.",
        fragment_type="key_idea", page_number=1,
        citation_intent=None, content_hash="s1",
    )
    assert len(_dedup_fragments_by_prefix([short], min_prefix=100)) == 1
