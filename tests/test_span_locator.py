"""Tests for the span/locator primitives behind `klemma repair`.

Covers `normalize_with_map` (index map through PDF-noise normalization),
`locate_fragment_span` (normalized-space match mapped back to raw sidecar
coordinates) and `derive_locator` (ГОСТ-style structural markers).
"""

from __future__ import annotations

import pytest

from klemma.literature.locator import derive_locator
from klemma.skills.extractor import locate_fragment_span
from klemma.text_normalize import normalize, normalize_with_map


class TestNormalizeWithMap:
    """The map must translate normalized indices back to raw ones exactly."""

    def test_empty(self) -> None:
        assert normalize_with_map("") == ("", [])

    def test_ascii_identity_map(self) -> None:
        norm, m = normalize_with_map("plain text")
        assert norm == "plain text"
        assert m == list(range(len("plain text")))

    def test_ligature_expansion_maps_to_raw_char(self) -> None:
        # 'ﬁ' (1 raw char at index 4) expands to "fi" (2 normalized chars).
        raw = "the ﬁnal"
        norm, m = normalize_with_map(raw)
        assert norm == "the final"
        assert m[4] == 4  # 'f' ← ligature
        assert m[5] == 4  # 'i' ← same ligature
        assert m[6] == 5  # 'n' ← raw index after the 1-char ligature

    def test_nbsp_becomes_space(self) -> None:
        norm, m = normalize_with_map("a b")
        assert norm == "a b"
        assert m == [0, 1, 2]

    def test_soft_hyphen_skipped_in_map(self) -> None:
        raw = "co­operation"
        norm, m = normalize_with_map(raw)
        assert norm == "cooperation"
        assert m[:4] == [0, 1, 3, 4]  # index 2 (soft hyphen) never appears

    def test_line_break_hyphen_rejoined(self) -> None:
        raw = "fore-\ncast"
        norm, m = normalize_with_map(raw)
        assert norm == "forecast"
        assert m == [0, 1, 2, 3, 6, 7, 8, 9]  # '-\n' (4, 5) dropped

    def test_whitespace_run_maps_to_first_ws_char(self) -> None:
        raw = "a  \n  b"
        norm, m = normalize_with_map(raw)
        assert norm == "a b"
        assert m == [0, 1, 6]  # collapsed space ← first ws char of the run

    def test_decomposed_cyrillic_composes(self) -> None:
        # "и" + combining breve composes into "й" exactly as normalize() does.
        raw = "й est"
        norm, m = normalize_with_map(raw)
        assert norm == normalize(raw) == "й est"
        assert m[0] == 0  # composed char maps to the sequence start

    @pytest.mark.parametrize(
        "raw",
        [
            "the ﬁnal eﬀective forecast",
            "co­operation and fore-\ncast skill",
            "  leading\t\tand   trailing  ",
            "a b c",
            "«Умные» кавычки — и тире",
        ],
    )
    def test_output_equals_normalize(self, raw: str) -> None:
        norm, m = normalize_with_map(raw)
        assert norm == normalize(raw)
        assert len(m) == len(norm)
        # Map is monotonically non-decreasing — spans stay well-formed.
        assert all(m[i] <= m[i + 1] for i in range(len(m) - 1))


class TestLocateFragmentSpan:
    def test_exact_match_plain(self) -> None:
        src = "Intro sentence. The model achieves 92% accuracy here. Outro."
        span = locate_fragment_span("achieves 92% accuracy", src)
        assert span is not None
        start, end = span
        assert src[start:end] == "achieves 92% accuracy"

    def test_exact_match_through_hyphenation(self) -> None:
        src = "Intro text. The fore-\ncast skill is poor. End."
        span = locate_fragment_span("forecast skill is poor", src)
        assert span is not None
        start, end = span
        # Span covers the raw (noisy) region, hyphen and newline included.
        assert src[start:end] == "fore-\ncast skill is poor"

    def test_exact_match_through_ligature(self) -> None:
        src = "the ﬁnal eﬀective forecast was accurate"
        span = locate_fragment_span("final effective forecast", src)
        assert span is not None
        start, end = span
        assert normalize(src[start:end]) == "final effective forecast"

    def test_fuzzy_rescue_single_char_swap(self) -> None:
        src = "decomposition separates overestimation from underestimation errors."
        span = locate_fragment_span(
            "decompositioa separates overestimation from underestimation", src
        )
        assert span is not None
        start, end = span
        assert start == 0
        assert "overestimation" in src[start:end]

    def test_miss_returns_none(self) -> None:
        src = "Arctic sea ice concentration is predicted using a neural network."
        assert locate_fragment_span(
            "Transformers achieve state-of-the-art on ImageNet", src
        ) is None

    def test_empty_inputs(self) -> None:
        assert locate_fragment_span("", "some text") is None
        assert locate_fragment_span("fragment", "") is None
        assert locate_fragment_span("   ", "some text") is None


GOST_TEXT = (
    "1 Область применения\n"
    "Настоящий стандарт устанавливает требования к методам прогнозов.\n"
    "\n"
    "3.4 Определение требуемой обеспеченности\n"
    "Определение требуемой обеспеченности и эффективности метода на основе "
    "оперативных (независимых) данных.\n"
    "\n"
    "Таблица 2 — Нормы продолжительности испытаний\n"
    "Долгосрочные прогнозы — не менее двух лет.\n"
    "\n"
    "Приложение А\n"
    "Справочные материалы по оценке кромки льда.\n"
)


class TestDeriveLocator:
    def test_clause_number(self) -> None:
        offset = GOST_TEXT.find("эффективности метода")
        assert derive_locator(GOST_TEXT, offset) == "п. 3.4"

    def test_clause_number_span_on_heading_line(self) -> None:
        offset = GOST_TEXT.find("требуемой обеспеченности")  # inside "3.4 ..." line
        assert derive_locator(GOST_TEXT, offset) == "п. 3.4"

    def test_table_caption(self) -> None:
        offset = GOST_TEXT.find("Долгосрочные прогнозы")
        assert derive_locator(GOST_TEXT, offset) == "табл. 2"

    def test_appendix(self) -> None:
        offset = GOST_TEXT.find("Справочные материалы")
        assert derive_locator(GOST_TEXT, offset) == "Приложение А"

    def test_single_number_is_not_a_clause(self) -> None:
        # "1 Область применения" — bare number, must not become "п. 1";
        # falls through to the page fallback.
        offset = GOST_TEXT.find("устанавливает требования")
        assert derive_locator(GOST_TEXT, offset, page=3) == "с. 3"

    def test_fallback_page(self) -> None:
        assert derive_locator("просто сплошной текст без структуры", 10, page=17) == "с. 17"

    def test_no_marker_no_page_returns_none(self) -> None:
        assert derive_locator("просто сплошной текст без структуры", 10) is None

    def test_nested_clause_number(self) -> None:
        text = "7.1.3.8 Инерционный прогноз принимается за стандарт.\nПри заблаговременности до трёх суток.\n"
        offset = text.find("трёх суток")
        assert derive_locator(text, offset) == "п. 7.1.3.8"

    def test_empty_text(self) -> None:
        assert derive_locator("", 0, page=2) == "с. 2"
        assert derive_locator("", 0) is None
