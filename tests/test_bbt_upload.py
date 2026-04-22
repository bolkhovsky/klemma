"""Tests for the BetterBibTeX JSON upload parser."""

from __future__ import annotations

import json

import pytest

from klemma.literature.bbt_upload import parse_bbt_upload

# ---------------------------------------------------------------------------
# Basic parsing
# ---------------------------------------------------------------------------


def _bbt(items: list[dict]) -> bytes:
    """Wrap items in a BBT-style JSON document."""
    return json.dumps({"items": items}).encode("utf-8")


def test_parse_minimal():
    data = _bbt([
        {
            "itemType": "journalArticle",
            "citationKey": "voronina2023",
            "title": "Основные направления",
            "creators": [{"creatorType": "author", "lastName": "Воронина"}],
            "date": "2023",
            "DOI": "10.1234/abc",
        }
    ])
    entries = parse_bbt_upload(data)
    assert len(entries) == 1
    e = entries[0]
    assert e.citekey == "voronina2023"
    assert e.title == "Основные направления"
    assert e.first_author_lastname == "Воронина"
    assert e.year == 2023
    assert e.doi == "10.1234/abc"


def test_multiple_items():
    data = _bbt([
        {"itemType": "journalArticle", "citationKey": "a2020", "title": "A"},
        {"itemType": "book", "citationKey": "b2021", "title": "B"},
    ])
    entries = parse_bbt_upload(data)
    assert [e.citekey for e in entries] == ["a2020", "b2021"]


# ---------------------------------------------------------------------------
# Skip rules
# ---------------------------------------------------------------------------


def test_skips_attachments_and_notes():
    data = _bbt([
        {"itemType": "attachment", "citationKey": "att", "title": "PDF"},
        {"itemType": "note", "citationKey": "note", "title": "Note"},
        {"itemType": "journalArticle", "citationKey": "good", "title": "X"},
    ])
    entries = parse_bbt_upload(data)
    assert [e.citekey for e in entries] == ["good"]


def test_skips_items_without_citekey():
    data = _bbt([
        {"itemType": "journalArticle", "title": "No key"},
        {"itemType": "journalArticle", "citationKey": "", "title": "Empty key"},
        {"itemType": "journalArticle", "citationKey": "real", "title": "OK"},
    ])
    entries = parse_bbt_upload(data)
    assert [e.citekey for e in entries] == ["real"]


# ---------------------------------------------------------------------------
# DOI normalization
# ---------------------------------------------------------------------------


def test_doi_url_prefix_stripped():
    data = _bbt([
        {"itemType": "journalArticle", "citationKey": "x",
         "DOI": "https://doi.org/10.1234/ABC"},
    ])
    entries = parse_bbt_upload(data)
    assert entries[0].doi == "10.1234/abc"


def test_doi_dx_prefix_stripped():
    data = _bbt([
        {"itemType": "journalArticle", "citationKey": "x",
         "DOI": "http://dx.doi.org/10.5555/xyz"},
    ])
    entries = parse_bbt_upload(data)
    assert entries[0].doi == "10.5555/xyz"


def test_doi_missing_is_none():
    data = _bbt([{"itemType": "journalArticle", "citationKey": "x"}])
    assert parse_bbt_upload(data)[0].doi is None


def test_doi_empty_is_none():
    data = _bbt([{"itemType": "journalArticle", "citationKey": "x", "DOI": "   "}])
    assert parse_bbt_upload(data)[0].doi is None


# ---------------------------------------------------------------------------
# Author extraction
# ---------------------------------------------------------------------------


def test_first_author_of_many():
    data = _bbt([{
        "itemType": "journalArticle", "citationKey": "x",
        "creators": [
            {"creatorType": "author", "lastName": "Smith"},
            {"creatorType": "author", "lastName": "Doe"},
        ],
    }])
    assert parse_bbt_upload(data)[0].first_author_lastname == "Smith"


def test_editor_skipped_in_favor_of_author():
    data = _bbt([{
        "itemType": "book", "citationKey": "x",
        "creators": [
            {"creatorType": "editor", "lastName": "Editor"},
            {"creatorType": "author", "lastName": "Author"},
        ],
    }])
    assert parse_bbt_upload(data)[0].first_author_lastname == "Author"


def test_no_author_empty_string():
    data = _bbt([{
        "itemType": "book", "citationKey": "x",
        "creators": [{"creatorType": "editor", "lastName": "Only"}],
    }])
    assert parse_bbt_upload(data)[0].first_author_lastname == ""


# ---------------------------------------------------------------------------
# Year parsing from various date formats
# ---------------------------------------------------------------------------


def test_year_iso_date():
    data = _bbt([{"itemType": "journalArticle", "citationKey": "x", "date": "2023-06-15"}])
    assert parse_bbt_upload(data)[0].year == 2023


def test_year_plain_4digit():
    data = _bbt([{"itemType": "journalArticle", "citationKey": "x", "date": "2023"}])
    assert parse_bbt_upload(data)[0].year == 2023


def test_year_freeform():
    data = _bbt([{"itemType": "journalArticle", "citationKey": "x", "date": "Spring 2024"}])
    assert parse_bbt_upload(data)[0].year == 2024


def test_year_missing():
    data = _bbt([{"itemType": "journalArticle", "citationKey": "x"}])
    assert parse_bbt_upload(data)[0].year is None


def test_year_non_date_string():
    data = _bbt([{"itemType": "journalArticle", "citationKey": "x", "date": "forthcoming"}])
    assert parse_bbt_upload(data)[0].year is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_invalid_json_raises_value_error():
    with pytest.raises(ValueError, match="Invalid BBT JSON"):
        parse_bbt_upload(b"not json at all")


def test_non_utf8_raises_value_error():
    with pytest.raises(ValueError, match="UTF-8"):
        parse_bbt_upload(b"\xff\xfeinvalid utf-8")


def test_missing_items_field_returns_empty():
    # Valid JSON but no "items" key
    assert parse_bbt_upload(b'{"version": "1.0"}') == []


def test_items_not_list_returns_empty():
    assert parse_bbt_upload(b'{"items": "not a list"}') == []
