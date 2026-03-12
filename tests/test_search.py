"""Tests for search.py — SearchProvider protocol, S2 backend, factory."""

from unittest.mock import MagicMock, patch

from klemma.search import (
    ChainSearchProvider,
    CrossRefSearchProvider,
    OpenAlexSearchProvider,
    S2SearchProvider,
    SearchProvider,
    SearchResult,
    create_search,
)


class TestSearchResult:
    def test_defaults(self):
        r = SearchResult(title="Test Paper")
        assert r.title == "Test Paper"
        assert r.authors == ""
        assert r.year is None
        assert r.abstract == ""
        assert r.doi == ""
        assert r.pdf_url == ""
        assert r.source_api == ""

    def test_full(self):
        r = SearchResult(
            title="Paper",
            authors="Smith, Jones",
            year=2023,
            abstract="Abstract text",
            doi="10.1234/test",
            pdf_url="https://example.com/paper.pdf",
            source_api="s2",
        )
        assert r.year == 2023
        assert r.doi == "10.1234/test"


class TestS2SearchProvider:
    def test_protocol_conformance(self):
        provider = S2SearchProvider()
        assert isinstance(provider, SearchProvider)
        assert provider.backend_name == "s2"

    @patch("klemma.literature.metadata.lookup_s2")
    def test_resolve_found(self, mock_lookup):
        mock_lookup.return_value = {
            "title": "Found Paper",
            "authors": "Doe, J.",
            "year": 2022,
            "abstract": "An abstract",
            "doi": "10.5555/found",
        }
        provider = S2SearchProvider()
        result = provider.resolve("Found Paper")
        assert result is not None
        assert result.title == "Found Paper"
        assert result.authors == "Doe, J."
        assert result.year == 2022
        assert result.doi == "10.5555/found"
        assert result.source_api == "s2"
        mock_lookup.assert_called_once_with("Found Paper")

    @patch("klemma.literature.metadata.lookup_s2")
    def test_resolve_not_found(self, mock_lookup):
        mock_lookup.return_value = None
        provider = S2SearchProvider()
        result = provider.resolve("Unknown Paper")
        assert result is None

    @patch("klemma.evaluation.resolvers.resolve_pdf_url")
    def test_resolve_pdf_url_found(self, mock_resolve):
        mock_result = MagicMock()
        mock_result.pdf_url = "https://arxiv.org/pdf/2106.12345"
        mock_resolve.return_value = mock_result

        provider = S2SearchProvider()
        url = provider.resolve_pdf_url("Test Paper", "Smith", 2021)
        assert url == "https://arxiv.org/pdf/2106.12345"

    @patch("klemma.evaluation.resolvers.resolve_pdf_url")
    def test_resolve_pdf_url_not_found(self, mock_resolve):
        mock_result = MagicMock()
        mock_result.pdf_url = ""
        mock_resolve.return_value = mock_result

        provider = S2SearchProvider()
        url = provider.resolve_pdf_url("Test Paper")
        assert url is None


class TestCreateSearch:
    def test_s2_backend(self):
        provider = create_search({"backend": "s2", "throttle": 5.0})
        assert provider is not None
        assert isinstance(provider, S2SearchProvider)
        assert provider.backend_name == "s2"

    def test_empty_backend_returns_none(self):
        assert create_search({"backend": ""}) is None

    def test_no_backend_returns_none(self):
        assert create_search({}) is None

    def test_empty_config_returns_none(self):
        assert create_search(None) is None

    def test_unknown_backend_returns_none(self):
        assert create_search({"backend": "unknown"}) is None

    def test_crossref_backend(self):
        provider = create_search({"backend": "crossref"})
        assert provider is not None
        assert isinstance(provider, CrossRefSearchProvider)
        assert provider.backend_name == "crossref"

    def test_auto_backend_creates_chain(self):
        provider = create_search({"backend": "auto"})
        assert provider is not None
        assert isinstance(provider, ChainSearchProvider)
        assert "s2" in provider.backend_name
        assert "crossref" in provider.backend_name


class TestCrossRefSearchProvider:
    def test_protocol_conformance(self):
        provider = CrossRefSearchProvider()
        assert isinstance(provider, SearchProvider)
        assert provider.backend_name == "crossref"

    @patch("requests.get")
    def test_resolve_found(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "message": {
                    "items": [{
                        "title": ["Arctic Sea Ice Predictions"],
                        "DOI": "10.1234/arctic",
                        "author": [
                            {"family": "Smith", "given": "J."},
                            {"family": "Jones", "given": "K."},
                        ],
                        "published-print": {"date-parts": [[2021]]},
                    }]
                }
            },
        )
        provider = CrossRefSearchProvider()
        result = provider.resolve("Arctic Sea Ice Predictions")
        assert result is not None
        assert result.doi == "10.1234/arctic"
        assert result.year == 2021
        assert result.source_api == "crossref"
        assert "Smith" in result.authors

    @patch("requests.get")
    def test_resolve_no_match(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"items": []}},
        )
        provider = CrossRefSearchProvider()
        assert provider.resolve("Nonexistent Paper XYZ") is None

    @patch("requests.get")
    def test_resolve_api_error(self, mock_get):
        mock_get.side_effect = Exception("Connection timeout")
        provider = CrossRefSearchProvider()
        assert provider.resolve("Some Paper") is None


class TestChainSearchProvider:
    def test_first_provider_wins(self):
        p1 = MagicMock()
        p1.backend_name = "first"
        p1.resolve.return_value = SearchResult(title="Found", source_api="first")

        p2 = MagicMock()
        p2.backend_name = "second"

        chain = ChainSearchProvider([p1, p2])
        result = chain.resolve("Test")
        assert result is not None
        assert result.source_api == "first"
        p2.resolve.assert_not_called()

    def test_fallback_on_first_failure(self):
        p1 = MagicMock()
        p1.backend_name = "first"
        p1.resolve.side_effect = Exception("429 rate limit")

        p2 = MagicMock()
        p2.backend_name = "second"
        p2.resolve.return_value = SearchResult(title="Found", source_api="second")

        chain = ChainSearchProvider([p1, p2])
        result = chain.resolve("Test")
        assert result is not None
        assert result.source_api == "second"

    def test_fallback_on_first_returns_none(self):
        p1 = MagicMock()
        p1.backend_name = "first"
        p1.resolve.return_value = None

        p2 = MagicMock()
        p2.backend_name = "second"
        p2.resolve.return_value = SearchResult(title="Found", source_api="second")

        chain = ChainSearchProvider([p1, p2])
        result = chain.resolve("Test")
        assert result is not None
        assert result.source_api == "second"

    def test_all_fail_returns_none(self):
        p1 = MagicMock()
        p1.backend_name = "first"
        p1.resolve.return_value = None

        p2 = MagicMock()
        p2.backend_name = "second"
        p2.resolve.return_value = None

        chain = ChainSearchProvider([p1, p2])
        assert chain.resolve("Test") is None

    def test_backend_name_joined(self):
        p1 = MagicMock()
        p1.backend_name = "s2"
        p2 = MagicMock()
        p2.backend_name = "crossref"
        chain = ChainSearchProvider([p1, p2])
        assert chain.backend_name == "s2+crossref"

    def test_pdf_url_fallback(self):
        p1 = MagicMock()
        p1.backend_name = "first"
        p1.resolve_pdf_url.return_value = None

        p2 = MagicMock()
        p2.backend_name = "second"
        p2.resolve_pdf_url.return_value = "https://example.com/paper.pdf"

        chain = ChainSearchProvider([p1, p2])
        url = chain.resolve_pdf_url("Test")
        assert url == "https://example.com/paper.pdf"


class TestOpenAlexSearchProvider:
    def test_protocol_conformance(self):
        provider = OpenAlexSearchProvider()
        assert isinstance(provider, SearchProvider)
        assert provider.backend_name == "openalex"

    def test_reconstruct_abstract(self):
        inverted = {
            "Sea": [0],
            "ice": [1],
            "prediction": [2],
            "methods": [3],
        }
        result = OpenAlexSearchProvider._reconstruct_abstract(inverted)
        assert result == "Sea ice prediction methods"

    def test_reconstruct_abstract_empty(self):
        assert OpenAlexSearchProvider._reconstruct_abstract(None) == ""
        assert OpenAlexSearchProvider._reconstruct_abstract({}) == ""

    def test_extract_authors(self):
        authorships = [
            {"author": {"display_name": "Jane Smith"}},
            {"author": {"display_name": "John Doe"}},
        ]
        result = OpenAlexSearchProvider._extract_authors(authorships)
        assert result == "Jane Smith, John Doe"

    def test_extract_authors_empty(self):
        assert OpenAlexSearchProvider._extract_authors([]) == ""

    @patch("requests.get")
    def test_resolve_found(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "results": [{
                    "title": "Arctic Sea Ice Predictions Using ML",
                    "doi": "https://doi.org/10.1234/arctic",
                    "publication_year": 2022,
                    "authorships": [
                        {"author": {"display_name": "Anna Petrov"}},
                    ],
                    "abstract_inverted_index": {
                        "Machine": [0], "learning": [1], "for": [2], "ice": [3],
                    },
                    "open_access": {"oa_url": ""},
                    "primary_location": {},
                }]
            },
        )
        provider = OpenAlexSearchProvider(mailto="test@example.com")
        result = provider.resolve("Arctic Sea Ice Predictions Using ML")
        assert result is not None
        assert result.title == "Arctic Sea Ice Predictions Using ML"
        assert result.doi == "10.1234/arctic"
        assert result.year == 2022
        assert result.source_api == "openalex"
        assert "Anna Petrov" in result.authors
        assert "Machine" in result.abstract

    @patch("requests.get")
    def test_resolve_no_match(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": []},
        )
        provider = OpenAlexSearchProvider()
        assert provider.resolve("Completely Nonexistent Paper XYZ") is None

    @patch("requests.get")
    def test_resolve_api_error(self, mock_get):
        mock_get.side_effect = Exception("Network error")
        provider = OpenAlexSearchProvider()
        assert provider.resolve("Some Paper") is None

    @patch("requests.get")
    def test_resolve_pdf_url_from_oa(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "results": [{
                    "title": "A Paper Title Here",
                    "open_access": {"oa_url": "https://arxiv.org/pdf/2301.12345"},
                    "primary_location": {},
                }]
            },
        )
        provider = OpenAlexSearchProvider()
        url = provider.resolve_pdf_url("A Paper Title Here")
        assert url == "https://arxiv.org/pdf/2301.12345"

    @patch("requests.get")
    def test_resolve_pdf_url_none(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": []},
        )
        provider = OpenAlexSearchProvider()
        assert provider.resolve_pdf_url("Nonexistent Paper") is None

    def test_mailto_in_user_agent(self):
        provider = OpenAlexSearchProvider(mailto="user@example.com")
        headers = provider._headers()
        assert "user@example.com" in headers["User-Agent"]

    def test_doi_prefix_stripped(self):
        """DOI URL prefix is stripped from the result."""
        with patch("requests.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "results": [{
                        "title": "Test Paper About Climate",
                        "doi": "https://doi.org/10.5678/climate",
                        "publication_year": 2023,
                        "authorships": [],
                        "abstract_inverted_index": None,
                        "open_access": {},
                        "primary_location": {},
                    }]
                },
            )
            provider = OpenAlexSearchProvider()
            result = provider.resolve("Test Paper About Climate")
            assert result is not None
            assert result.doi == "10.5678/climate"


class TestCreateSearchWithOpenAlex:
    def test_openalex_backend(self):
        provider = create_search({"backend": "openalex", "mailto": "me@example.com"})
        assert provider is not None
        assert isinstance(provider, OpenAlexSearchProvider)
        assert provider.backend_name == "openalex"

    def test_auto_chain_includes_openalex(self):
        provider = create_search({"backend": "auto"})
        assert isinstance(provider, ChainSearchProvider)
        assert "openalex" in provider.backend_name
        assert "s2" in provider.backend_name
