"""Tests for reference_parser.py (#76 CiteQ onboarding)."""

from __future__ import annotations

from klemma.literature.reference_parser import ParsedReference, parse_reference, parse_references

# ---------------------------------------------------------------------------
# APA-style references
# ---------------------------------------------------------------------------


def test_apa_basic():
    ref = parse_reference(
        "Smith, J., & Jones, K. (2020). Machine Learning for NLP. Nature, 123, 45-67."
    )
    assert ref.authors == "Smith, J., & Jones, K."
    assert ref.year == 2020
    assert ref.title == "Machine Learning for NLP"
    assert ref.journal == "Nature"


def test_apa_with_doi():
    ref = parse_reference(
        "Cohan, A. (2019). SPECTER: Document-Level Representation. ACL. https://doi.org/10.18653/v1/2020.acl-main.207"
    )
    assert ref.year == 2019
    assert ref.title == "SPECTER: Document-Level Representation"
    assert "10.18653" in ref.doi


def test_apa_single_author():
    ref = parse_reference(
        "Pautasso, M. (2013). Ten simple rules for writing a literature review. PLoS Computational Biology, 9(7)."
    )
    assert ref.authors == "Pautasso, M."
    assert ref.year == 2013
    assert "literature review" in ref.title.lower()
    assert "PLoS" in ref.journal


# ---------------------------------------------------------------------------
# Numbered references
# ---------------------------------------------------------------------------


def test_numbered_bracket():
    ref = parse_reference(
        "[1] Wagner, P.M. Sea ice information and forecast needs. Cold Regions Sci. 2020;140:103–108."
    )
    assert ref.year == 2020
    assert "Wagner" in ref.authors or "Wagner" in ref.title


def test_numbered_dot():
    ref = parse_reference(
        "3. Bidenko S. Neural network approaches for ice prediction. J. Marine Systems. 2019."
    )
    assert ref.year == 2019


# ---------------------------------------------------------------------------
# DOI and URL extraction
# ---------------------------------------------------------------------------


def test_doi_extraction():
    ref = parse_reference(
        "Smith (2020). Title. Journal. doi:10.1234/test.5678"
    )
    assert ref.doi == "10.1234/test.5678"


def test_url_extraction():
    ref = parse_reference(
        "Smith (2020). Title. Available at https://arxiv.org/abs/2101.12345"
    )
    assert ref.url == "https://arxiv.org/abs/2101.12345"


def test_doi_url_extraction():
    ref = parse_reference(
        "Smith (2020). Title. https://doi.org/10.1234/test"
    )
    assert "10.1234/test" in ref.doi


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_string():
    ref = parse_reference("")
    assert ref.raw == ""
    assert ref.year is None
    assert ref.title == ""


def test_minimal_reference():
    ref = parse_reference("Some random text without structure")
    assert ref.raw == "Some random text without structure"


def test_year_extraction_from_middle():
    ref = parse_reference("A method published in 2018 for ice forecasting.")
    assert ref.year == 2018


def test_no_year():
    ref = parse_reference("Smith J. A paper about methods. Some Journal.")
    assert ref.year is None


# ---------------------------------------------------------------------------
# Batch parsing
# ---------------------------------------------------------------------------


def test_parse_references_numbered():
    text = """[1] Smith, J. (2020). First paper. Nature.
[2] Jones, K. (2019). Second paper. Science.
[3] Lee, M. (2021). Third paper. PNAS."""
    refs = parse_references(text)
    assert len(refs) == 3
    assert refs[0].year == 2020
    assert refs[1].year == 2019
    assert refs[2].year == 2021


def test_parse_references_paragraph():
    text = """Smith, J. (2020). First paper. Nature, 1, 2-3.

Jones, K. (2019). Second paper. Science, 4(5), 67-89."""
    refs = parse_references(text)
    assert len(refs) == 2


def test_parse_references_filters_short():
    text = """[1] Smith, J. (2020). A real reference with enough content. Nature.
[2] Too short.
[3] Jones, K. (2019). Another real reference. Science, 123."""
    refs = parse_references(text)
    assert len(refs) == 2  # "Too short." filtered out


def test_parsed_reference_dataclass():
    ref = ParsedReference(raw="test", authors="A", year=2020, title="T")
    assert ref.raw == "test"
    assert ref.journal == ""
    assert ref.doi == ""
