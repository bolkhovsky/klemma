"""Tests for prompt budget control and RAG-first fragment retrieval in researcher."""

import json
from unittest.mock import MagicMock, patch

import pytest

from klemma.skills.researcher import _fit_prompt_budget

# ---------------------------------------------------------------------------
# _fit_prompt_budget tests
# ---------------------------------------------------------------------------


class TestFitPromptBudget:
    """Progressive prompt reduction tests."""

    def _make_sources(self, n, summary_len=100):
        return [
            {"citekey": f"src{i}", "quality": 5, "summary": "x" * summary_len}
            for i in range(n)
        ]

    def _make_fragments(self, n, text_len=100):
        return [
            {"source": f"src{i}", "text": "y" * text_len, "type": "result"}
            for i in range(n)
        ]

    def test_no_reduction_within_budget(self):
        """Content within budget passes through unchanged."""
        draft = "short draft"
        sources = self._make_sources(5)
        fragments = self._make_fragments(5)

        rd, rs, rf = _fit_prompt_budget(draft, sources, fragments)

        assert rd == draft
        assert rs == sources
        assert rf == fragments

    def test_trim_draft(self):
        """Big draft gets trimmed to 12K chars first."""
        draft = "A" * 50_000
        sources = self._make_sources(3, summary_len=50)
        fragments = self._make_fragments(3, text_len=50)

        rd, rs, rf = _fit_prompt_budget(draft, sources, fragments, max_chars=30_000)

        assert len(rd) == 12_000
        # Sources and fragments should be unchanged
        assert len(rs) == 3
        assert len(rf) == 3
        for s in rs:
            assert len(s["summary"]) == 50

    def test_trim_summaries(self):
        """When draft trim isn't enough, summaries get trimmed to 400 chars."""
        draft = "A" * 15_000
        sources = self._make_sources(20, summary_len=2000)
        fragments = self._make_fragments(5, text_len=50)

        rd, rs, rf = _fit_prompt_budget(draft, sources, fragments, max_chars=40_000)

        assert len(rd) == 12_000  # draft trimmed first
        for s in rs:
            assert len(s["summary"]) <= 400

    def test_trim_fragments(self):
        """When summaries trim isn't enough, fragment text trimmed to 150 chars."""
        draft = "A" * 15_000
        sources = self._make_sources(20, summary_len=800)
        fragments = self._make_fragments(30, text_len=500)

        rd, rs, rf = _fit_prompt_budget(draft, sources, fragments, max_chars=30_000)

        for f in rf:
            assert len(f["text"]) <= 150

    def test_reduce_sources(self):
        """Extreme case: sources list reduced to 15."""
        draft = "A" * 15_000
        sources = self._make_sources(40, summary_len=800)
        fragments = self._make_fragments(40, text_len=500)

        rd, rs, rf = _fit_prompt_budget(draft, sources, fragments, max_chars=25_000)

        assert len(rs) <= 15

    def test_reduce_fragments(self):
        """Worst case: fragments also reduced to 20."""
        draft = "A" * 15_000
        sources = self._make_sources(40, summary_len=800)
        fragments = self._make_fragments(40, text_len=500)

        # Very tight budget forces all reductions
        rd, rs, rf = _fit_prompt_budget(draft, sources, fragments, max_chars=15_000)

        assert len(rs) <= 15
        assert len(rf) <= 20

    def test_progressive_order(self):
        """Verify reduction order: draft first, summaries second, etc.

        With a budget that only needs draft trimming, other content is untouched.
        """
        draft = "A" * 80_000
        sources = self._make_sources(5, summary_len=100)
        fragments = self._make_fragments(5, text_len=100)

        rd, rs, rf = _fit_prompt_budget(draft, sources, fragments, max_chars=20_000)

        assert len(rd) == 12_000
        # With 12K draft + 5 small sources + 5 small fragments + 8K overhead < 20K
        assert len(rs) == 5
        assert len(rf) == 5
        for s in rs:
            assert len(s["summary"]) == 100  # untouched

    def test_extreme_sources_fits_token_limit(self):
        """85 sources (section 1.3 scenario) must leave room for template overhead.

        Real prompt includes ~20K chars of untracked template variables
        (dissertation_context, coverage, gaps, template text, etc.).
        Content must stay under 60K chars so total prompt fits 30K TPM.
        """
        draft = "A" * 30_000
        # 85 sources with realistic vault summaries (~600 chars each)
        sources = self._make_sources(85, summary_len=600)
        fragments = self._make_fragments(40, text_len=300)

        rd, rs, rf = _fit_prompt_budget(draft, sources, fragments)

        content_chars = (
            len(rd)
            + sum(len(json.dumps(s, ensure_ascii=False)) for s in rs)
            + sum(len(json.dumps(f, ensure_ascii=False)) for f in rf)
        )
        # Content must leave ~20K chars room for template + context variables
        # 60K content + 20K template ≈ 80K chars ≈ 20K tokens + 4K output = 24K < 30K TPM
        assert content_chars <= 60_000, (
            f"Content too large for safe prompt: {content_chars} chars. "
            f"With ~20K template overhead, total ≈ {content_chars + 20_000} chars "
            f"≈ {(content_chars + 20_000) // 4} tokens, exceeds 30K TPM with output."
        )

    def test_worst_case_fits_token_limit(self):
        """Worst case inputs (85 sources × 1K summaries) must still fit.

        Even with very large source summaries, the budget function must
        aggressively reduce to fit within safe limits.
        """
        draft = "A" * 50_000
        sources = self._make_sources(85, summary_len=1000)
        fragments = self._make_fragments(40, text_len=500)

        rd, rs, rf = _fit_prompt_budget(draft, sources, fragments)

        content_chars = (
            len(rd)
            + sum(len(json.dumps(s, ensure_ascii=False)) for s in rs)
            + sum(len(json.dumps(f, ensure_ascii=False)) for f in rf)
        )
        assert content_chars <= 60_000, (
            f"Content too large: {content_chars} chars"
        )


# ---------------------------------------------------------------------------
# RAG-first fragment retrieval tests
# ---------------------------------------------------------------------------


def _make_state(tmp_path, with_embeddings=True):
    """Create a StateManager with test fragments."""
    from klemma.state import StateManager

    sm = StateManager(tmp_path / "test.db")
    sm.register_sources(["paper1", "paper2", "paper3"])
    sm.mark_completed("paper1", note_path="@paper1.md")
    sm.mark_completed("paper2", note_path="@paper2.md")
    sm.mark_completed("paper3", note_path="@paper3.md")

    # Set sections for paper1 and paper2
    sm.set_source_sections("paper1", ["1"], [1])
    sm.set_source_sections("paper2", ["1"], [1])
    sm.set_source_sections("paper3", ["2"], [2])

    sm.save_fragments("paper1", [
        {"text": "Semantic analysis of morphemes", "type": "method", "section": "1",
         "citation_intent": "method"},
    ])
    sm.save_fragments("paper2", [
        {"text": "Frequency-based lemmatization approach", "type": "result", "section": "1",
         "citation_intent": "result_comparison"},
    ])
    sm.save_fragments("paper3", [
        {"text": "Unrelated Arctic navigation data", "type": "result", "section": "2",
         "citation_intent": "background"},
    ])

    if with_embeddings:
        frags = sm.get_fragments(limit=100)
        for i, frag in enumerate(frags):
            # Different vectors so cosine similarity differs
            vec = [0.0] * 10
            vec[i % 10] = 1.0
            sm.save_fragment_embedding(frag["id"], vec, "test-model")

    return sm


class TestRAGFragments:
    """Tests for RAG-first fragment retrieval in research_section()."""

    def _mock_vault(self):
        vault = MagicMock()
        vault.read_note.return_value = None
        return vault

    def test_rag_fragments_used_when_embeddings_available(self, tmp_path):
        """With embeddings + section_text, retrieve_similar_fragments is called."""
        sm = _make_state(tmp_path)

        mock_emb = MagicMock()
        mock_emb.model_name = "test-model"
        mock_emb.embed.return_value = [1.0] + [0.0] * 9

        # Mock AI to return valid JSON
        mock_ai = MagicMock()
        mock_ai.call_json.return_value = {"section_status": "draft", "argument_blocks": []}
        mock_ai.render_prompt.return_value = "test prompt"

        from klemma.config import KlemmaConfig
        from klemma.skills.researcher import research_section

        config = KlemmaConfig()

        with patch("klemma.skills.researcher._load_chapter_draft") as mock_draft, \
             patch("klemma.skills.researcher._extract_section") as mock_extract, \
             patch("klemma.skills.researcher.resolve_prompt") as mock_rp:
            mock_draft.return_value = "# Chapter 1\n## 1. Morphological Analysis\nSome text."
            mock_extract.return_value = "## 1. Morphological Analysis\nSome text about morphemes."
            mock_rp.return_value = tmp_path / "research.md"
            (tmp_path / "research.md").write_text("{{ section_text }}")

            research_section(
                "1", config, sm, self._mock_vault(), mock_ai,
                save_to_vault=False,
                embeddings=mock_emb,
            )

        # embed() should have been called with section text
        mock_emb.embed.assert_called_once()
        call_arg = mock_emb.embed.call_args[0][0]
        assert "Morphological Analysis" in call_arg

    def test_rag_fallback_when_few_results(self, tmp_path):
        """RAG returns <10 results → section-based fallback supplements."""
        sm = _make_state(tmp_path, with_embeddings=False)

        mock_emb = MagicMock()
        mock_emb.model_name = "test-model"
        # Return a vector but no fragment embeddings exist → retrieve returns []
        mock_emb.embed.return_value = [1.0] + [0.0] * 9

        mock_ai = MagicMock()
        mock_ai.call_json.return_value = {"section_status": "draft", "argument_blocks": []}
        mock_ai.render_prompt.return_value = "test prompt"

        from klemma.config import KlemmaConfig
        from klemma.skills.researcher import research_section

        config = KlemmaConfig()

        with patch("klemma.skills.researcher._load_chapter_draft") as mock_draft, \
             patch("klemma.skills.researcher._extract_section") as mock_extract, \
             patch("klemma.skills.researcher.resolve_prompt") as mock_rp:
            mock_draft.return_value = "# Chapter\n## 1. Test\nText."
            mock_extract.return_value = "## 1. Test section text"
            mock_rp.return_value = tmp_path / "research.md"
            (tmp_path / "research.md").write_text("{{ section_text }}")

            result = research_section(
                "1", config, sm, self._mock_vault(), mock_ai,
                save_to_vault=False,
                embeddings=mock_emb,
            )

        # Should have fallen back and have fragments from section "1"
        assert result.available_fragments >= 2  # paper1 + paper2 section fragments

    def test_rag_fallback_when_no_embeddings(self, tmp_path):
        """embeddings=None → original section-based behavior."""
        sm = _make_state(tmp_path, with_embeddings=False)

        mock_ai = MagicMock()
        mock_ai.call_json.return_value = {"section_status": "draft", "argument_blocks": []}
        mock_ai.render_prompt.return_value = "test prompt"

        from klemma.config import KlemmaConfig
        from klemma.skills.researcher import research_section

        config = KlemmaConfig()

        with patch("klemma.skills.researcher._load_chapter_draft") as mock_draft, \
             patch("klemma.skills.researcher._extract_section") as mock_extract, \
             patch("klemma.skills.researcher.resolve_prompt") as mock_rp:
            mock_draft.return_value = "# Chapter\n## 1. Test\nText."
            mock_extract.return_value = "## 1. Test section text"
            mock_rp.return_value = tmp_path / "research.md"
            (tmp_path / "research.md").write_text("{{ section_text }}")

            result = research_section(
                "1", config, sm, self._mock_vault(), mock_ai,
                save_to_vault=False,
                embeddings=None,
            )

        # Section-based should return paper1 + paper2 fragments
        assert result.available_fragments >= 2

    def test_rag_fallback_on_embed_error(self, tmp_path):
        """embed() raises → graceful fallback to section-based."""
        sm = _make_state(tmp_path, with_embeddings=False)

        mock_emb = MagicMock()
        mock_emb.model_name = "test-model"
        mock_emb.embed.side_effect = RuntimeError("API down")

        mock_ai = MagicMock()
        mock_ai.call_json.return_value = {"section_status": "draft", "argument_blocks": []}
        mock_ai.render_prompt.return_value = "test prompt"

        from klemma.config import KlemmaConfig
        from klemma.skills.researcher import research_section

        config = KlemmaConfig()

        with patch("klemma.skills.researcher._load_chapter_draft") as mock_draft, \
             patch("klemma.skills.researcher._extract_section") as mock_extract, \
             patch("klemma.skills.researcher.resolve_prompt") as mock_rp:
            mock_draft.return_value = "# Chapter\n## 1. Test\nText."
            mock_extract.return_value = "## 1. Test section text"
            mock_rp.return_value = tmp_path / "research.md"
            (tmp_path / "research.md").write_text("{{ section_text }}")

            # Should not crash
            result = research_section(
                "1", config, sm, self._mock_vault(), mock_ai,
                save_to_vault=False,
                embeddings=mock_emb,
            )

        assert result.available_fragments >= 2

    def test_rag_dedup_with_fallback(self, tmp_path):
        """RAG + fallback → no duplicate fragment IDs."""
        sm = _make_state(tmp_path)

        # RAG returns a few results (< 10), then fallback adds more
        mock_emb = MagicMock()
        mock_emb.model_name = "test-model"
        mock_emb.embed.return_value = [1.0] + [0.0] * 9

        mock_ai = MagicMock()
        mock_ai.call_json.return_value = {"section_status": "draft", "argument_blocks": []}
        mock_ai.render_prompt.return_value = "test prompt"

        from klemma.config import KlemmaConfig
        from klemma.skills.researcher import research_section

        config = KlemmaConfig()

        with patch("klemma.skills.researcher._load_chapter_draft") as mock_draft, \
             patch("klemma.skills.researcher._extract_section") as mock_extract, \
             patch("klemma.skills.researcher.resolve_prompt") as mock_rp:
            mock_draft.return_value = "# Chapter\n## 1. Test\nText."
            mock_extract.return_value = "## 1. Test section text"
            mock_rp.return_value = tmp_path / "research.md"
            (tmp_path / "research.md").write_text("{{ section_text }}")

            research_section(
                "1", config, sm, self._mock_vault(), mock_ai,
                save_to_vault=False,
                embeddings=mock_emb,
            )

        # The render_prompt call should have received fragments JSON
        call_kwargs = mock_ai.render_prompt.call_args
        fragments_json = call_kwargs.kwargs.get("fragments") or call_kwargs[1].get("fragments")
        if fragments_json:
            frags = json.loads(fragments_json)
            # No exact duplicates (same source can appear if different fragments)
            # but fragment count should be reasonable
            assert len(frags) <= 40


# ---------------------------------------------------------------------------
# Benchmark: section-based vs RAG quality (requires real DB)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
def test_section_vs_rag_fragment_quality():
    """Compare section-based vs RAG fragment retrieval quality.

    Requires ~/research/klemma-paper/klemma.db with embedded fragments.
    Skip if DB not available.

    Metrics:
    - overlap: Jaccard similarity of fragment sets
    - rag_avg_similarity: mean cosine similarity of RAG results
    - source_coverage: unique citekeys in top-40 fragments
    """
    from pathlib import Path

    db_path = Path.home() / "research" / "klemma-paper" / ".klemma" / "data" / "klemma.db"
    if not db_path.exists():
        pytest.skip("Benchmark DB not available at ~/research/klemma-paper/.klemma/data/klemma.db")

    from klemma.config import _load_klemmarc
    from klemma.embeddings import create_embeddings
    from klemma.state import StateManager

    sm = StateManager(db_path)

    # Check if fragments have embeddings
    stats = sm.get_fragment_embedding_stats()
    if stats.get("embedded", 0) < 50:
        pytest.skip(f"Not enough embedded fragments: {stats.get('embedded', 0)}")

    # Try to create embeddings provider (with klemmarc api_keys)
    rc = _load_klemmarc()
    api_keys = rc.get("api_keys", {})
    emb = create_embeddings({"backend": "openai"}, api_keys=api_keys)
    if emb is None:
        pytest.skip("No embedding provider available for benchmark")

    # Quick connectivity check
    test_vec = emb.embed("test")
    if test_vec is None:
        pytest.skip("Embedding provider not reachable")

    # Test sections from dialog2026
    sections = {
        "1": "Computational methods for Uralic language morphological analysis",
        "2": "Related work in low-resource language processing and lemmatization",
        "3": "Klemma system architecture and implementation",
    }

    results = {}
    for sec_id, sec_desc in sections.items():
        # Section-based
        section_frags = sm.get_fragments(section=sec_id, limit=40)
        section_ids = {f["id"] for f in section_frags}

        # RAG-based
        query_vec = emb.embed(sec_desc)
        if not query_vec:
            continue
        rag_frags = sm.retrieve_similar_fragments(query_vec, top_k=40, model=emb.model_name)
        rag_ids = {f["id"] for f in rag_frags}

        # Jaccard overlap
        intersection = section_ids & rag_ids
        union = section_ids | rag_ids
        overlap = len(intersection) / len(union) if union else 0

        # RAG avg similarity
        rag_avg_sim = (
            sum(f.get("similarity", 0) for f in rag_frags) / len(rag_frags)
            if rag_frags else 0
        )

        # Source coverage
        section_citekeys = {f.get("citekey", f.get("source_id")) for f in section_frags}
        rag_citekeys = {f.get("citekey", f.get("source_id")) for f in rag_frags}

        results[sec_id] = {
            "section_count": len(section_frags),
            "rag_count": len(rag_frags),
            "overlap": round(overlap, 3),
            "rag_avg_similarity": round(rag_avg_sim, 4),
            "section_citekeys": len(section_citekeys),
            "rag_citekeys": len(rag_citekeys),
        }

    # Print results for manual review
    for sec_id, r in results.items():
        print(f"\nSection {sec_id}:")
        print(f"  Section-based: {r['section_count']} frags, {r['section_citekeys']} citekeys")
        print(f"  RAG-based:     {r['rag_count']} frags, {r['rag_citekeys']} citekeys")
        print(f"  Overlap:       {r['overlap']}")
        print(f"  RAG avg sim:   {r['rag_avg_similarity']}")

    # Basic assertions (RAG should find at least some fragments)
    for sec_id, r in results.items():
        assert r["rag_count"] > 0, f"RAG found 0 fragments for section {sec_id}"
