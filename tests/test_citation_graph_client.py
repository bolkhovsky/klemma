"""Tests for the OpenAlex citation-graph client (literature/citation_graph.py).

Mocks the module's `requests` (the module does `import requests`). Distinct from
`test_citation_graph.py`, which tests the citation-link *storage* layer.
"""

from unittest.mock import MagicMock, patch

from klemma.literature.citation_graph import (
    SeedWork,
    _bare_id,
    _reconstruct_abstract,
    fetch_citation_graph,
    fetch_seed_work,
)


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


class TestPureHelpers:
    def test_bare_id(self):
        assert _bare_id("https://openalex.org/W123") == "W123"
        assert _bare_id("W123") == "W123"
        assert _bare_id("") == ""

    def test_reconstruct_abstract_uses_positions(self):
        assert _reconstruct_abstract({"b": [1], "a": [0]}) == "a b"
        assert _reconstruct_abstract(None) == ""
        assert _reconstruct_abstract({}) == ""


class TestFetchSeedWork:
    def test_by_doi_normalizes_referenced_works(self):
        work = {
            "id": "https://openalex.org/W2342323825",
            "doi": "https://doi.org/10.1002/grl.x",
            "title": "Predictability of the Arctic sea ice edge",
            "referenced_works": [
                "https://openalex.org/W111",
                "https://openalex.org/W222",
            ],
        }
        with patch("klemma.literature.citation_graph.requests") as mock_req:
            mock_req.get.return_value = _resp(work)
            seed = fetch_seed_work(doi="10.1002/grl.x", mailto="me@test.org")

        assert seed is not None
        assert seed.openalex_id == "W2342323825"
        assert seed.referenced_works == ["W111", "W222"]  # bare ids
        # polite-pool mailto reaches the request
        ua = mock_req.get.call_args.kwargs["headers"]["User-Agent"]
        assert "me@test.org" in ua

    def test_no_doi_applies_title_match_gate(self):
        """Without a DOI, the first hit must pass the fuzzy title gate."""
        results = {
            "results": [
                {"id": "https://openalex.org/W1", "doi": "", "title": "A totally different chemistry paper", "referenced_works": []},
                {"id": "https://openalex.org/W2", "doi": "", "title": "Predictability of the Arctic sea ice edge", "referenced_works": ["https://openalex.org/W9"]},
            ]
        }
        with patch("klemma.literature.citation_graph.requests") as mock_req:
            mock_req.get.return_value = _resp(results)
            seed = fetch_seed_work(title="Predictability of the Arctic sea ice edge")

        assert seed is not None
        assert seed.openalex_id == "W2"  # not the non-matching W1
        assert seed.referenced_works == ["W9"]

    def test_no_match_returns_none(self):
        results = {"results": [{"id": "https://openalex.org/W1", "title": "Unrelated", "referenced_works": []}]}
        with patch("klemma.literature.citation_graph.requests") as mock_req:
            mock_req.get.return_value = _resp(results)
            assert fetch_seed_work(title="Predictability of the Arctic sea ice edge") is None

    def test_network_error_returns_none(self):
        with patch("klemma.literature.citation_graph.requests") as mock_req:
            mock_req.get.side_effect = Exception("boom")
            assert fetch_seed_work(doi="10.1/x") is None


class TestFetchCitationGraph:
    def _candidate_work(self, wid, title, doi=""):
        return {
            "id": f"https://openalex.org/{wid}",
            "doi": doi,
            "title": title,
            "publication_year": 2020,
            "authorships": [{"author": {"display_name": "Jane Doe"}}],
            "abstract_inverted_index": {"Sea": [0], "ice": [1]},
            "cited_by_count": 7,
            "primary_location": {"source": {"display_name": "GRL"}},
        }

    def test_refs_and_citers_merge_with_both_relation(self):
        seed = SeedWork(openalex_id="WSEED", doi="10.1/seed", title="Seed", referenced_works=["WA", "WSHARED"])
        refs_resp = _resp({"results": [self._candidate_work("WA", "Ref A"), self._candidate_work("WSHARED", "Shared")], "meta": {"count": 2}})
        citers_resp = _resp({"results": [self._candidate_work("WC", "Citer C"), self._candidate_work("WSHARED", "Shared")], "meta": {"count": 2}})

        with patch("klemma.literature.citation_graph.requests") as mock_req:
            mock_req.get.side_effect = [refs_resp, citers_resp]
            cands = fetch_citation_graph(seed, mailto="me@test.org")

        by_id = {c.openalex_id: c for c in cands}
        assert by_id["WA"].relation == "ref"
        assert by_id["WC"].relation == "cites"
        assert by_id["WSHARED"].relation == "both"  # in refs and citers
        # candidate fields parsed
        assert by_id["WA"].venue == "GRL"
        assert by_id["WA"].abstract == "Sea ice"
        assert by_id["WA"].first_author == "Jane Doe"

        # request shape: type:article on both, openalex: batch on refs, cites: on citers
        urls = [c.args[0] for c in mock_req.get.call_args_list]
        assert any("filter=openalex:WA|WSHARED,type:article" in u for u in urls)
        assert any("filter=cites:WSEED,type:article" in u for u in urls)

    def test_referenced_works_chunked_over_50(self):
        refs = [f"W{i}" for i in range(120)]
        seed = SeedWork(openalex_id="WSEED", doi="", title="Seed", referenced_works=refs)
        empty = _resp({"results": [], "meta": {"count": 0}})
        with patch("klemma.literature.citation_graph.requests") as mock_req:
            mock_req.get.return_value = empty
            fetch_citation_graph(seed)
        # 120 refs → 3 batches of 50/50/20 + 1 citers call = 4 GETs
        assert mock_req.get.call_count == 4

    def test_network_error_returns_empty(self):
        seed = SeedWork(openalex_id="WSEED", doi="", title="Seed", referenced_works=["WA"])
        with patch("klemma.literature.citation_graph.requests") as mock_req:
            mock_req.get.side_effect = Exception("boom")
            assert fetch_citation_graph(seed) == []
