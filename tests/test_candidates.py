"""Tests for benchmark candidate discovery and DEV mode hints."""

import pytest

from klemma.evaluation.candidates import (
    CandidateScore,
    discover_candidates,
    format_candidate_hint,
)
from klemma.state import StateManager


@pytest.fixture
def state(tmp_path):
    return StateManager(tmp_path / "test.db")


def _seed_sources_with_citations(state, n_sources=5, n_refs_each=4):
    """Create sources with citation links for candidate discovery."""
    import hashlib
    keys = [f"source{i}" for i in range(n_sources)]
    state.register_sources(keys)
    for key in keys:
        # Mark completed
        state.mark_completed(key, note_path=f"@{key}.md", quality_score=4)
        state.set_pdf_path(key, f"/fake/{key}.pdf")
        # Create citation links pointing to other sources (some in_library)
        refs = []
        for j in range(n_refs_each):
            target = f"source{(int(key[-1]) + j + 1) % n_sources}"
            title = f"Title of {target}"
            refs.append({
                "citekey": target,
                "title": title,
                "title_hash": hashlib.md5(title.lower().encode()).hexdigest(),
                "authors": "Author",
                "year": 2020,
                "citation_intent": ["background", "method", "result_comparison"][j % 3],
                "in_library": True,
            })
        state.save_citation_links(key, refs)


class TestDiscoverCandidates:
    def test_finds_candidates(self, state):
        _seed_sources_with_citations(state, n_sources=5, n_refs_each=4)
        candidates = discover_candidates(state, limit=5)
        assert len(candidates) > 0
        assert all(c.in_library_citations >= 3 for c in candidates)

    def test_sorted_by_score_descending(self, state):
        _seed_sources_with_citations(state, n_sources=5, n_refs_each=5)
        candidates = discover_candidates(state, limit=10)
        scores = [c.score for c in candidates]
        assert scores == sorted(scores, reverse=True)

    def test_already_benchmarked_penalized(self, state):
        _seed_sources_with_citations(state, n_sources=5, n_refs_each=4)
        # Benchmark one source
        state.save_benchmark_run(
            results={}, results_summary={}, paper_citekey="source0",
        )
        candidates = discover_candidates(state, limit=10)
        benchmarked = [c for c in candidates if c.citekey == "source0"]
        non_benchmarked = [c for c in candidates if c.citekey != "source0"]
        if benchmarked and non_benchmarked:
            assert benchmarked[0].already_benchmarked is True
            # Benchmarked should have lower score than similar non-benchmarked
            assert benchmarked[0].score < non_benchmarked[0].score

    def test_no_candidates_when_insufficient_citations(self, state):
        state.register_sources(["lonely"])
        state.mark_completed("lonely", note_path="@lonely.md")
        candidates = discover_candidates(state, limit=5)
        assert len(candidates) == 0

    def test_limit_respected(self, state):
        _seed_sources_with_citations(state, n_sources=10, n_refs_each=5)
        candidates = discover_candidates(state, limit=3)
        assert len(candidates) <= 3

    def test_has_pdf_flag(self, state):
        _seed_sources_with_citations(state, n_sources=5, n_refs_each=4)
        candidates = discover_candidates(state, limit=10)
        assert all(c.has_pdf for c in candidates)

    def test_pre_provided_benchmarked_set(self, state):
        _seed_sources_with_citations(state, n_sources=5, n_refs_each=4)
        candidates = discover_candidates(
            state, limit=10, benchmarked_citekeys={"source0", "source1"},
        )
        for c in candidates:
            if c.citekey in {"source0", "source1"}:
                assert c.already_benchmarked is True


class TestFormatCandidateHint:
    def test_empty_list(self):
        assert format_candidate_hint([]) == ""

    def test_single_candidate(self):
        cands = [CandidateScore(citekey="foo2020", in_library_citations=5)]
        hint = format_candidate_hint(cands)
        assert "foo2020" in hint
        assert "5 refs" in hint
        assert hint.startswith("[dim]")

    def test_limit_respected(self):
        cands = [
            CandidateScore(citekey=f"paper{i}", in_library_citations=i)
            for i in range(5)
        ]
        hint = format_candidate_hint(cands, limit=2)
        assert "paper0" in hint
        assert "paper1" in hint
        assert "paper2" not in hint
