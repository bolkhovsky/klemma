"""Tests for skills/suggester.py — suggest_acquisitions."""

from unittest.mock import MagicMock

from klemma.search import SearchResult
from klemma.skills.suggester import _parse_sections, suggest_acquisitions


def _make_gap(title="Paper A", authors="Smith", year=2022, score=5.0, sections='["1.2", "2.3"]'):
    return {
        "ref_title": title,
        "ref_authors": authors,
        "ref_year": year,
        "score": score,
        "dissertation_sections": sections,
    }


def _mock_search(resolve_return=None, pdf_url_return=None):
    search = MagicMock()
    search.backend_name = "mock"
    search.resolve.return_value = resolve_return
    search.resolve_pdf_url.return_value = pdf_url_return
    return search


class TestSuggestAcquisitions:
    def test_empty_gaps(self):
        search = _mock_search()
        result = suggest_acquisitions([], search, limit=5)
        assert result == []

    def test_basic_resolution(self):
        gaps = [_make_gap(score=8.0)]
        sr = SearchResult(
            title="Paper A", authors="Smith", year=2022,
            doi="10.1234/a", source_api="s2",
        )
        search = _mock_search(
            resolve_return=sr,
            pdf_url_return="https://arxiv.org/pdf/2022.00001",
        )

        candidates = suggest_acquisitions(gaps, search, limit=5)

        assert len(candidates) == 1
        c = candidates[0]
        assert c.ref_title == "Paper A"
        assert c.score == 8.0
        assert c.pdf_url == "https://arxiv.org/pdf/2022.00001"
        assert "klemma acquire" in c.acquire_cmd
        assert "https://arxiv.org/pdf/2022.00001" in c.acquire_cmd
        assert "-s 1.2" in c.acquire_cmd
        assert "-s 2.3" in c.acquire_cmd

    def test_search_returns_none(self):
        """When search finds nothing, candidate still created without pdf/doi."""
        gaps = [_make_gap()]
        search = _mock_search(resolve_return=None)

        candidates = suggest_acquisitions(gaps, search)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.search_result is None
        assert c.pdf_url == ""
        assert c.acquire_cmd == ""

    def test_doi_only_no_pdf(self):
        """When DOI is found but no open-access PDF, acquire_cmd uses DOI URL."""
        gaps = [_make_gap()]
        sr = SearchResult(
            title="Paper A", authors="Smith", year=2022,
            doi="10.1234/a", source_api="s2",
        )
        search = _mock_search(resolve_return=sr, pdf_url_return=None)

        candidates = suggest_acquisitions(gaps, search)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.doi == "10.1234/a"
        assert "doi.org/10.1234/a" in c.acquire_cmd

    def test_limit_respected(self):
        gaps = [_make_gap(title=f"Paper {i}", score=10 - i) for i in range(10)]
        search = _mock_search(resolve_return=None)

        candidates = suggest_acquisitions(gaps, search, limit=3)
        assert len(candidates) == 3

    def test_sorted_by_score(self):
        gaps = [
            _make_gap(title="Low", score=1.0),
            _make_gap(title="High", score=9.0),
            _make_gap(title="Mid", score=5.0),
        ]
        search = _mock_search(resolve_return=None)

        candidates = suggest_acquisitions(gaps, search, limit=3)
        assert candidates[0].ref_title == "High"
        assert candidates[1].ref_title == "Mid"
        assert candidates[2].ref_title == "Low"

    def test_search_error_handled(self):
        """Search exceptions are caught, candidate still added."""
        gaps = [_make_gap()]
        search = _mock_search()
        search.resolve.side_effect = Exception("API timeout")

        candidates = suggest_acquisitions(gaps, search)
        assert len(candidates) == 1
        assert candidates[0].search_result is None

    def test_json_sections_parsed(self):
        """JSON-encoded sections from DB are parsed correctly."""
        gaps = [_make_gap(sections='["1.3", "2.3"]')]
        sr = SearchResult(title="Paper A", doi="10.1234/a", source_api="s2")
        search = _mock_search(resolve_return=sr, pdf_url_return=None)

        candidates = suggest_acquisitions(gaps, search)
        c = candidates[0]
        assert c.sections == ["1.3", "2.3"]
        assert "-s 1.3" in c.acquire_cmd
        assert "-s 2.3" in c.acquire_cmd

    def test_group_concat_sections(self):
        """GROUP_CONCAT of multiple JSON arrays parsed correctly."""
        gaps = [_make_gap(sections='["1.3", "2.3"],["1.4"]')]
        search = _mock_search(resolve_return=None)

        candidates = suggest_acquisitions(gaps, search)
        assert candidates[0].sections == ["1.3", "2.3", "1.4"]


class TestParseSections:
    def test_empty(self):
        assert _parse_sections("") == []

    def test_single_json_array(self):
        assert _parse_sections('["1.3", "2.3"]') == ["1.3", "2.3"]

    def test_group_concat_arrays(self):
        assert _parse_sections('["1.3", "2.3"],["1.4"]') == ["1.3", "2.3", "1.4"]

    def test_deduplicates(self):
        assert _parse_sections('["1.3"],["1.3", "2.3"]') == ["1.3", "2.3"]

    def test_plain_csv_fallback(self):
        assert _parse_sections("1.3, 2.3") == ["1.3", "2.3"]
