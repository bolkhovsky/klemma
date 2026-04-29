"""Tests for verbatim validation window size.

Cap raised to 1 MB in #382 — fragments quoting text past the old 150K cap
no longer get falsely downgraded on long normative documents (e.g.
abuzyarov2011, 339K chars). The cap remains as a backstop for pathological
multi-MB inputs only.
"""

from __future__ import annotations


class TestVerbatimCapConstants:
    def test_small_threshold_is_100k(self):
        from klemma.api.constants import VERBATIM_VALIDATION_CAP_SMALL
        assert VERBATIM_VALIDATION_CAP_SMALL == 100_000

    def test_large_cap_is_1mb(self):
        """Cap raised from 150K to 1 MB in #382. Covers all academic papers
        plus most book-length documents while bounding RAM for pathological
        inputs (multi-MB scans / OCR dumps)."""
        from klemma.api.constants import VERBATIM_VALIDATION_CAP_LARGE
        assert VERBATIM_VALIDATION_CAP_LARGE == 1_000_000

    def test_large_cap_greater_than_small_threshold(self):
        from klemma.api.constants import (
            VERBATIM_VALIDATION_CAP_LARGE,
            VERBATIM_VALIDATION_CAP_SMALL,
        )
        assert VERBATIM_VALIDATION_CAP_LARGE > VERBATIM_VALIDATION_CAP_SMALL


class TestVerbatimWindowSelection:
    """Unit tests for the window-selection logic (mirrored from tasks.py)."""

    def _window(self, text: str) -> str:
        from klemma.api.constants import (
            VERBATIM_VALIDATION_CAP_LARGE,
            VERBATIM_VALIDATION_CAP_SMALL,
        )
        return text if len(text) < VERBATIM_VALIDATION_CAP_SMALL else text[:VERBATIM_VALIDATION_CAP_LARGE]

    def test_at_99k_uses_full_text(self):
        text = "x" * 99_000
        result = self._window(text)
        assert result is text  # same object — no slicing

    def test_at_100k_boundary_goes_to_else_branch(self):
        # 100K == threshold → goes to else branch → pdf_text[:1M]
        # But since text is only 100K, the slice returns the full 100K text
        text = "x" * 100_000
        result = self._window(text)
        assert len(result) == 100_000

    def test_at_200k_within_new_cap(self):
        """200K papers used to be sliced to 150K; now pass through fully."""
        text = "x" * 200_000
        result = self._window(text)
        assert len(result) == 200_000

    def test_at_500k_within_new_cap(self):
        """Book-length docs (500K = abuzyarov2011 territory) pass through fully."""
        text = "x" * 500_000
        result = self._window(text)
        assert len(result) == 500_000

    def test_at_2mb_caps_to_1mb(self):
        """Pathological inputs (>1 MB) still capped as RAM backstop."""
        text = "x" * 2_000_000
        result = self._window(text)
        assert len(result) == 1_000_000

    def test_fragment_at_60k_in_small_pdf_is_found(self):
        """Fragment beyond old 50K cap is now reachable in small PDFs."""
        fragment = "unique verbatim fragment text xyz"
        prefix = "a" * 60_000
        text = prefix + fragment + "b" * 39_000  # total: ~99K — small PDF
        window = self._window(text)
        assert fragment in window

    def test_fragment_at_160k_in_large_pdf_is_now_found(self):
        """Regression for #382: fragment at 160K used to be sliced off
        (150K cap) and falsely downgraded. With the 1 MB cap it survives."""
        fragment = "unique verbatim fragment text def"
        prefix = "a" * 160_000
        text = prefix + fragment + "b" * 40_000  # total: ~200K
        window = self._window(text)
        assert fragment in window

    def test_fragment_at_900k_in_book_length_doc_is_found(self):
        """Book-length normative document: fragment near end is still within cap."""
        fragment = "unique verbatim fragment text ghi"
        prefix = "a" * 900_000
        text = prefix + fragment + "b" * 50_000  # total: ~950K
        window = self._window(text)
        assert fragment in window

    def test_fragment_past_1mb_in_pathological_input_is_not_found(self):
        """Pathological 2MB input: fragments past the 1MB cap remain unreachable.
        Acceptable trade-off for RAM safety on misuploaded files."""
        fragment = "unique verbatim fragment text jkl"
        prefix = "a" * 1_100_000
        text = prefix + fragment + "b" * 100_000
        window = self._window(text)
        assert fragment not in window
