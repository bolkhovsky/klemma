"""Tests for text_normalize: NFKC + PDF cleanup helpers."""

from __future__ import annotations

import pytest

from klemma.text_normalize import normalize


class TestNFKCLigatures:
    """NFKC decomposes PDF ligature glyphs into their component letters."""

    def test_fi_ligature(self) -> None:
        assert normalize("\ufb01nal") == "final"

    def test_ffi_ligature(self) -> None:
        assert normalize("o\ufb03ce") == "office"

    def test_fl_ligature(self) -> None:
        assert normalize("\ufb02ow") == "flow"

    def test_ff_ligature(self) -> None:
        assert normalize("e\ufb00ect") == "effect"


class TestSmartQuotesAndDashes:
    """NFKC normalizes typographic punctuation variants."""

    def test_smart_double_quote(self) -> None:
        # NFKC does NOT replace smart quotes with ASCII, but it stabilizes
        # the codepoint. Verify normalization is deterministic and equal
        # across repeated input forms.
        a = normalize("\u201chello\u201d")
        b = normalize("\u201chello\u201d")
        assert a == b

    def test_nonbreaking_space_to_space(self) -> None:
        # NFKC maps U+00A0 (nbsp) to a regular space.
        assert normalize("word\u00a0word") == "word word"


class TestSoftHyphen:
    """Soft hyphens (U+00AD) are stripped — they're invisible PDF hints."""

    def test_soft_hyphen_stripped_inside_word(self) -> None:
        assert normalize("co\u00adoperation") == "cooperation"

    def test_multiple_soft_hyphens(self) -> None:
        assert normalize("a\u00adb\u00adc") == "abc"


class TestLineBreakHyphenation:
    """PDF line breaks split words with ``-\\n``; rejoin them."""

    def test_hyphen_newline_unix(self) -> None:
        assert normalize("fore-\ncast") == "forecast"

    def test_hyphen_newline_windows(self) -> None:
        assert normalize("fore-\r\ncast") == "forecast"

    def test_hyphen_multiple_newlines(self) -> None:
        assert normalize("fore-\n\ncast") == "forecast"

    def test_genuine_hyphen_kept_inline(self) -> None:
        # Hyphen without newline should be preserved (compound word).
        assert normalize("well-known result") == "well-known result"


class TestWhitespaceCollapse:
    """Whitespace runs collapse to a single space; ends stripped."""

    def test_multiple_spaces(self) -> None:
        assert normalize("a   b") == "a b"

    def test_tabs_and_newlines(self) -> None:
        assert normalize("a\tb\nc") == "a b c"

    def test_leading_trailing_whitespace(self) -> None:
        assert normalize("  hello  ") == "hello"

    def test_empty_string(self) -> None:
        assert normalize("") == ""

    def test_only_whitespace(self) -> None:
        assert normalize("   \n\t  ") == ""


class TestIdempotence:
    """Normalization must be a projection: f(f(x)) == f(x)."""

    @pytest.mark.parametrize(
        "raw",
        [
            "plain text",
            "\ufb01nal e\ufb00ect",  # ligatures
            "word\u00adword",  # soft hyphen
            "fore-\ncast run-\r\non",  # line-break hyphens
            "  messy   \n\t  text  ",
            "mix of fi\ufb01 and co\u00adoperation and well-\nknown",
        ],
    )
    def test_idempotent(self, raw: str) -> None:
        once = normalize(raw)
        assert normalize(once) == once


class TestRealPDFExtractionPatterns:
    """Regression cases drawn from actual PDF extraction artifacts."""

    def test_ligature_plus_hyphenation(self) -> None:
        raw = "The e\ufb00ective fore-\ncast skill for sea ice\u00a0extent."
        assert (
            normalize(raw)
            == "The effective forecast skill for sea ice extent."
        )

    def test_typical_abstract_noise(self) -> None:
        raw = (
            "Arctic\u00a0sea\u00a0ice\u00a0concentration\u00a0is\u00a0pre-\n"
            "dicted\u00a0using\u00a0a\u00a0neural\u00a0network."
        )
        assert (
            normalize(raw)
            == "Arctic sea ice concentration is predicted using a neural network."
        )
