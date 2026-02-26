"""Tests for paper resolvers and benchmark preparation."""

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from klemma.evaluation.prepare import prepare_benchmark
from klemma.evaluation.resolvers import (
    ResolvedPaper,
    _titles_match,
    resolve_arxiv,
    resolve_crossref_doi,
    resolve_pdf_url,
    resolve_unpaywall,
)
from klemma.state import StateManager

# --- Resolver tests ---


class TestTitlesMatch:
    def test_exact_match(self):
        assert _titles_match("Deep Learning for NLP", "Deep Learning for NLP")

    def test_case_insensitive(self):
        assert _titles_match("Deep Learning", "deep learning")

    def test_partial_overlap(self):
        assert _titles_match(
            "Deep Learning for Natural Language Processing",
            "Deep Learning for Natural Language Tasks",
        )

    def test_no_match(self):
        assert not _titles_match("Deep Learning for NLP", "Quantum Computing Review")

    def test_empty_strings(self):
        assert not _titles_match("", "Something")
        assert not _titles_match("Something", "")


class TestResolveArxiv:
    @patch("klemma.evaluation.resolvers.requests.get")
    @patch("klemma.evaluation.resolvers.time.monotonic", return_value=1000.0)
    @patch("klemma.evaluation.resolvers._last_arxiv_call", 0.0)
    def test_successful_resolve(self, mock_time, mock_get):
        xml_response = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <title>Deep Learning for Natural Language Processing</title>
                <id>http://arxiv.org/abs/2301.12345</id>
                <published>2023-01-15T00:00:00Z</published>
                <author><name>John Doe</name></author>
                <link title="pdf" href="https://arxiv.org/pdf/2301.12345.pdf" rel="related"/>
            </entry>
        </feed>"""
        mock_resp = MagicMock()
        mock_resp.text = xml_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = resolve_arxiv("Deep Learning for Natural Language Processing")
        assert result is not None
        assert result.source == "arxiv"
        assert "2301.12345" in result.pdf_url
        assert result.year == 2023

    @patch("klemma.evaluation.resolvers.requests.get")
    @patch("klemma.evaluation.resolvers.time.monotonic", return_value=1000.0)
    @patch("klemma.evaluation.resolvers._last_arxiv_call", 0.0)
    def test_no_match(self, mock_time, mock_get):
        xml_response = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <title>Completely Unrelated Paper About Cooking</title>
                <id>http://arxiv.org/abs/9999.99999</id>
                <published>2020-01-01T00:00:00Z</published>
                <author><name>Chef</name></author>
            </entry>
        </feed>"""
        mock_resp = MagicMock()
        mock_resp.text = xml_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = resolve_arxiv("Deep Learning for NLP")
        assert result is None

    @patch("klemma.evaluation.resolvers.requests.get", side_effect=Exception("timeout"))
    @patch("klemma.evaluation.resolvers.time.monotonic", return_value=1000.0)
    @patch("klemma.evaluation.resolvers._last_arxiv_call", 0.0)
    def test_api_error(self, mock_time, mock_get):
        result = resolve_arxiv("Some Paper Title")
        assert result is None


class TestResolveCrossrefDoi:
    @patch("klemma.evaluation.resolvers.requests.get")
    def test_successful_resolve(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {
                "items": [
                    {
                        "title": ["Deep Learning for NLP"],
                        "DOI": "10.1234/test.2023",
                    }
                ]
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        doi = resolve_crossref_doi("Deep Learning for NLP")
        assert doi == "10.1234/test.2023"

    @patch("klemma.evaluation.resolvers.requests.get", side_effect=Exception("err"))
    def test_api_error(self, mock_get):
        assert resolve_crossref_doi("Some Paper") is None


class TestResolveUnpaywall:
    @patch("klemma.evaluation.resolvers.requests.get")
    def test_successful_resolve(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "best_oa_location": {
                "url_for_pdf": "https://example.com/paper.pdf",
            }
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        url = resolve_unpaywall("10.1234/test")
        assert url == "https://example.com/paper.pdf"

    @patch("klemma.evaluation.resolvers.requests.get", side_effect=Exception("err"))
    def test_api_error(self, mock_get):
        assert resolve_unpaywall("10.1234/test") is None


class TestResolvePdfUrl:
    @patch("klemma.evaluation.resolvers.resolve_arxiv")
    def test_arxiv_first(self, mock_arxiv):
        mock_arxiv.return_value = ResolvedPaper(
            title="Test", pdf_url="https://arxiv.org/pdf/1234.pdf", source="arxiv",
        )
        result = resolve_pdf_url("Test Paper")
        assert result.source == "arxiv"
        assert result.pdf_url

    @patch("klemma.evaluation.resolvers.resolve_arxiv", return_value=None)
    @patch("klemma.evaluation.resolvers.resolve_crossref_doi", return_value="10.1234/x")
    @patch("klemma.evaluation.resolvers.resolve_unpaywall", return_value="https://example.com/paper.pdf")
    def test_fallback_to_unpaywall(self, mock_unpay, mock_crossref, mock_arxiv):
        result = resolve_pdf_url("Test Paper")
        assert result.source == "unpaywall"
        assert result.pdf_url == "https://example.com/paper.pdf"

    @patch("klemma.evaluation.resolvers.resolve_arxiv", return_value=None)
    @patch("klemma.evaluation.resolvers.resolve_crossref_doi", return_value=None)
    def test_no_resolution(self, mock_crossref, mock_arxiv):
        result = resolve_pdf_url("Test Paper")
        assert result.pdf_url == ""
        assert result.source == ""


# --- Prepare tests ---


@pytest.fixture
def state(tmp_path):
    return StateManager(tmp_path / "test.db")


def _seed_paper_with_refs(state, paper_key="paper2020", n_in_lib=3, n_missing=2):
    """Create a paper with citation links (some in_library, some not)."""
    state.register_sources([paper_key])
    state.mark_completed(paper_key, note_path=f"@{paper_key}.md")

    # Register in-library targets
    in_lib_keys = [f"lib_ref{i}" for i in range(n_in_lib)]
    state.register_sources(in_lib_keys)

    refs = []
    for i, key in enumerate(in_lib_keys):
        title = f"Library Paper {i}"
        refs.append({
            "citekey": key,
            "title": title,
            "title_hash": hashlib.md5(title.lower().encode()).hexdigest(),
            "authors": "Author",
            "year": 2020,
            "citation_intent": "background",
            "in_library": True,
        })
    for i in range(n_missing):
        title = f"Missing Paper {i}"
        refs.append({
            "citekey": "",
            "title": title,
            "title_hash": hashlib.md5(title.lower().encode()).hexdigest(),
            "authors": "Unknown",
            "year": 2019,
            "citation_intent": "method",
            "in_library": False,
        })
    state.save_citation_links(paper_key, refs)
    return paper_key


class TestPrepareBenchmark:
    @patch("klemma.evaluation.prepare.resolve_pdf_url")
    def test_dry_run(self, mock_resolve, state):
        _seed_paper_with_refs(state)
        mock_resolve.return_value = ResolvedPaper(
            title="Missing Paper", pdf_url="https://example.com/x.pdf", source="arxiv",
        )
        result = prepare_benchmark(state, "paper2020", "/tmp/storage", dry_run=True)
        assert result.in_library == 3
        assert result.total_references == 5
        # Should not have fetched anything in dry_run
        assert result.fetched == 0
        resolved_refs = [r for r in result.references if r.status == "resolved"]
        assert len(resolved_refs) == 2

    @patch("klemma.evaluation.prepare.resolve_pdf_url")
    def test_no_citation_links(self, mock_resolve, state):
        state.register_sources(["lonely"])
        result = prepare_benchmark(state, "lonely", "/tmp/storage")
        assert result.total_references == 0

    @patch("klemma.evaluation.prepare.resolve_pdf_url")
    def test_unfetchable_refs(self, mock_resolve, state):
        _seed_paper_with_refs(state, n_in_lib=2, n_missing=3)
        mock_resolve.return_value = ResolvedPaper(title="Missing", pdf_url="", source="")
        result = prepare_benchmark(state, "paper2020", "/tmp/storage", dry_run=True)
        assert result.unfetchable == 3

    @patch("klemma.skills.acquirer.acquire_paper_local")
    @patch("klemma.evaluation.prepare.resolve_pdf_url")
    def test_actual_fetch(self, mock_resolve, mock_acquire, state):
        _seed_paper_with_refs(state, n_in_lib=1, n_missing=1)
        mock_resolve.return_value = ResolvedPaper(
            title="Paper X", pdf_url="https://arxiv.org/pdf/1234.pdf", source="arxiv",
        )
        mock_acquire.return_value = MagicMock(status="ok", citekey="acquired2020")
        result = prepare_benchmark(state, "paper2020", "/tmp/storage", dry_run=False)
        assert result.fetched == 1
        mock_acquire.assert_called_once()
