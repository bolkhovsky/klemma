"""Tests for verbatim validation window size (Part 3 of upload-pipeline-speedup).

Verifies that the cap logic in constants.py is used correctly:
- PDFs < 100K chars: validate against full text
- PDFs >= 100K chars: validate against first 150K chars
"""

from __future__ import annotations


class TestVerbatimCapConstants:
    def test_small_threshold_is_100k(self):
        from klemma.api.constants import VERBATIM_VALIDATION_CAP_SMALL
        assert VERBATIM_VALIDATION_CAP_SMALL == 100_000

    def test_large_cap_is_150k(self):
        from klemma.api.constants import VERBATIM_VALIDATION_CAP_LARGE
        assert VERBATIM_VALIDATION_CAP_LARGE == 150_000

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
        # 100K == threshold → goes to else branch → pdf_text[:150K]
        # But since text is only 100K, the slice returns the full 100K text
        text = "x" * 100_000
        result = self._window(text)
        assert len(result) == 100_000  # capped at 150K but text is shorter

    def test_at_101k_within_cap(self):
        # 101K > threshold, cap = 150K — since 101K < 150K, full text returned
        text = "x" * 101_000
        result = self._window(text)
        assert len(result) == 101_000  # within 150K cap

    def test_at_149k_within_cap(self):
        text = "x" * 149_000
        result = self._window(text)
        assert len(result) == 149_000  # 149K < 150K cap — full text

    def test_at_200k_caps_to_150k(self):
        text = "x" * 200_000
        result = self._window(text)
        assert len(result) == 150_000

    def test_fragment_at_60k_in_small_pdf_is_found(self):
        """Fragment beyond old 50K cap is now reachable in small PDFs."""
        fragment = "unique verbatim fragment text xyz"
        # Place fragment at position 60K
        prefix = "a" * 60_000
        text = prefix + fragment + "b" * 39_000  # total: ~99K — small PDF
        window = self._window(text)
        assert fragment in window

    def test_fragment_at_140k_in_large_pdf_is_found(self):
        """Fragment at 140K position is within 150K cap for large PDFs."""
        fragment = "unique verbatim fragment text abc"
        prefix = "a" * 140_000
        text = prefix + fragment + "b" * 60_000  # total: ~200K — large PDF
        window = self._window(text)
        assert fragment in window

    def test_fragment_at_160k_in_large_pdf_is_not_found(self):
        """Fragment at 160K is outside the 150K cap for large PDFs."""
        fragment = "unique verbatim fragment text def"
        prefix = "a" * 160_000
        text = prefix + fragment + "b" * 40_000  # total: ~200K
        window = self._window(text)
        assert fragment not in window
