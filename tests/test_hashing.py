"""Tests for content-addressable hashing utilities (ADR-014)."""

import pytest

from klemma.hashing import compute_content_hash, compute_pdf_hash, compute_prompt_hash


class TestComputePdfHash:
    def test_deterministic(self, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake pdf content for testing")
        assert compute_pdf_hash(pdf) == compute_pdf_hash(pdf)

    def test_different_content_different_hash(self, tmp_path):
        a = tmp_path / "a.pdf"
        b = tmp_path / "b.pdf"
        a.write_bytes(b"%PDF-1.4 content A")
        b.write_bytes(b"%PDF-1.4 content B")
        assert compute_pdf_hash(a) != compute_pdf_hash(b)

    def test_returns_hex_string(self, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        h = compute_pdf_hash(pdf)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA256 hex
        int(h, 16)  # valid hex

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            compute_pdf_hash(tmp_path / "nonexistent.pdf")


class TestComputeContentHash:
    def test_deterministic(self):
        h1 = compute_content_hash("smith2024", "Important finding about X", 5)
        h2 = compute_content_hash("smith2024", "Important finding about X", 5)
        assert h1 == h2

    def test_different_paper_different_hash(self):
        h1 = compute_content_hash("smith2024", "Same text", 1)
        h2 = compute_content_hash("jones2024", "Same text", 1)
        assert h1 != h2

    def test_different_text_different_hash(self):
        h1 = compute_content_hash("smith2024", "Text A", 1)
        h2 = compute_content_hash("smith2024", "Text B", 1)
        assert h1 != h2

    def test_different_page_different_hash(self):
        h1 = compute_content_hash("smith2024", "Same text", 1)
        h2 = compute_content_hash("smith2024", "Same text", 2)
        assert h1 != h2

    def test_none_page_uses_zero(self):
        h_none = compute_content_hash("smith2024", "text", None)
        h_zero = compute_content_hash("smith2024", "text", 0)
        assert h_none == h_zero

    def test_returns_hex_string(self):
        h = compute_content_hash("paper", "text", 1)
        assert isinstance(h, str)
        assert len(h) == 64
        int(h, 16)

    def test_empty_text(self):
        h = compute_content_hash("paper", "", None)
        assert isinstance(h, str)
        assert len(h) == 64


class TestComputePromptHash:
    def test_deterministic(self):
        h1 = compute_prompt_hash("Extract fragments from {{ title }}")
        h2 = compute_prompt_hash("Extract fragments from {{ title }}")
        assert h1 == h2

    def test_different_prompt_different_hash(self):
        h1 = compute_prompt_hash("prompt v1")
        h2 = compute_prompt_hash("prompt v2")
        assert h1 != h2

    def test_truncated_to_16_chars(self):
        h = compute_prompt_hash("any prompt text")
        assert len(h) == 16
        int(h, 16)

    def test_empty_prompt(self):
        h = compute_prompt_hash("")
        assert isinstance(h, str)
        assert len(h) == 16
