"""Tests for domain repositories — verify repos work independently via StateManager."""

import pytest

from klemma.state import StateManager


@pytest.fixture
def state(tmp_path):
    """Create a StateManager with a temporary database."""
    db_path = tmp_path / "test.db"
    return StateManager(db_path)


class TestRepositoryComposition:
    """Verify StateManager correctly composes and delegates to repositories."""

    def test_repos_are_accessible(self, state):
        assert state.sources is not None
        assert state.fragments is not None
        assert state.embeddings_store is not None
        assert state.gaps is not None
        assert state.citations is not None
        assert state.plans is not None
        assert state.prune is not None

    def test_source_repo_register_and_get(self, state):
        """Source repo: register + get roundtrip."""
        state.sources.register_sources(["test-key"])
        source = state.sources.get_source("test-key")
        assert source is not None
        assert source["id"] == "test-key"
        assert source["status"] == "pending"

    def test_facade_delegates_to_source_repo(self, state):
        """Facade method calls same repo method."""
        state.register_sources(["facade-key"])
        source = state.get_source("facade-key")
        assert source is not None
        assert source["id"] == "facade-key"

    def test_fragment_repo_save_and_stats(self, state):
        """Fragment repo: save + stats roundtrip."""
        state.sources.register_sources(["src-1"])
        state.fragments.save_fragments("src-1", [
            {"text": "Test fragment", "type": "key_idea", "section": "2.1",
             "relevance": 4, "citation_intent": "method"},
        ])
        stats = state.fragments.get_fragment_stats()
        assert stats["total"] == 1
        assert stats["by_type"]["key_idea"] == 1

    def test_embedding_repo_roundtrip(self, state):
        """Embedding repo: save + get roundtrip."""
        state.sources.register_sources(["emb-src"])
        vec = [0.1, 0.2, 0.3]
        state.embeddings_store.save_embedding("emb-src", vec, "test-model")
        result = state.embeddings_store.get_embedding("emb-src")
        assert result is not None
        retrieved_vec, model = result
        assert model == "test-model"
        assert len(retrieved_vec) == 3
        assert abs(retrieved_vec[0] - 0.1) < 1e-5

    def test_gaps_repo_save_and_summary(self, state):
        """Gaps repo: save + summary."""
        state.sources.register_sources(["gap-src"])
        state.gaps.save_reference_gaps("gap-src", [
            {"ref_authors": "Smith", "ref_year": 2020, "ref_title": "Test Paper",
             "why_relevant": "important", "citation_intent": "method"},
        ])
        summary = state.gaps.get_gap_summary()
        assert summary["open_count"] == 1

    def test_citations_repo_save_and_stats(self, state):
        """Citations repo: save + graph stats."""
        state.sources.register_sources(["cite-src"])
        state.citations.save_citation_links("cite-src", [
            {"title": "Cited Work", "authors": "Jones", "year": 2021,
             "citation_intent": "background", "in_library": False},
        ])
        stats = state.citations.get_citation_graph_stats()
        assert stats["total_links"] == 1
        assert stats["external"] == 1

    def test_plans_repo_save_and_get(self, state):
        """Plans repo: save + get."""
        state.plans.save_plan("Write intro", "Review papers")
        plan = state.plans.get_plan()
        assert plan is not None
        assert plan["dissertation_task"] == "Write intro"

    def test_prune_repo_save_and_summary(self, state):
        """Prune repo: save + summary."""
        state.sources.register_sources(["prune-src"])
        state.prune.save_prune_verdicts(
            drop=[{"citekey": "prune-src", "reason": "low quality"}],
            maybe=[],
        )
        summary = state.prune.get_prune_summary()
        assert summary["drop"] == 1

    def test_existing_source_ids(self, state):
        """New public method replaces old _conn() usage."""
        state.register_sources(["a", "b", "c"])
        ids = state.get_existing_source_ids()
        assert ids == {"a", "b", "c"}

    def test_sources_without_embeddings(self, state):
        """New public method replaces old _conn() usage."""
        state.register_sources(["s1", "s2"])
        state.mark_completed("s1", "/notes/s1.md")
        state.mark_completed("s2", "/notes/s2.md")
        state.save_embedding("s1", [0.1, 0.2], "model")
        without = state.get_sources_without_embeddings()
        assert "s2" in without
        assert "s1" not in without

    def test_set_source_sections_public(self, state):
        """Public set_source_sections replaces old _set_sections_inline."""
        state.register_sources(["sec-src"])
        state.set_source_sections("sec-src", ["2.1", "2.3"], [2])
        with state._conn() as conn:
            cur = conn.execute(
                "SELECT section FROM source_sections WHERE source_id='sec-src' ORDER BY section"
            )
            sections = [row["section"] for row in cur.fetchall()]
        assert "2.1" in sections
        assert "2.3" in sections

    def test_rerank_gaps_semantic_no_embeddings(self, state):
        """rerank_gaps_semantic returns gaps unchanged without embeddings."""
        gaps = [{"score": 10.0, "source_ids": "a,b"}]
        result = state.rerank_gaps_semantic(gaps, embeddings=None)
        assert result == gaps

    def test_delete_fragments_removes_all(self, state):
        """delete_fragments clears all fragments for a source."""
        state.sources.register_sources(["del-src"])
        state.fragments.save_fragments("del-src", [
            {"text": "Frag 1", "type": "key_idea", "section": "1.1", "relevance": 3},
            {"text": "Frag 2", "type": "method", "section": "1.2", "relevance": 4},
        ])
        assert state.fragments.get_fragment_stats()["total"] == 2
        state.fragments.delete_fragments("del-src")
        assert state.fragments.get_fragment_stats()["total"] == 0

    def test_delete_fragments_returns_count(self, state):
        """delete_fragments returns number of deleted rows."""
        state.sources.register_sources(["cnt-src"])
        state.fragments.save_fragments("cnt-src", [
            {"text": "F1", "type": "key_idea", "section": "2.1", "relevance": 3},
            {"text": "F2", "type": "key_idea", "section": "2.2", "relevance": 3},
            {"text": "F3", "type": "key_idea", "section": "2.3", "relevance": 3},
        ])
        deleted = state.fragments.delete_fragments("cnt-src")
        assert deleted == 3

    def test_get_completed_sources(self, state):
        """get_completed_sources returns only completed sources."""
        state.sources.register_sources(["done-1", "done-2", "pending-1"])
        state.sources.mark_completed("done-1", "/notes/done-1.md")
        state.sources.mark_completed("done-2", "/notes/done-2.md")
        completed = state.sources.get_completed_sources()
        assert "done-1" in completed
        assert "done-2" in completed
        assert "pending-1" not in completed

    def test_get_completed_sources_facade(self, state):
        """Facade delegates get_completed_sources to source repo."""
        state.register_sources(["c1", "c2", "p1"])
        state.mark_completed("c1", "/notes/c1.md")
        state.mark_completed("c2", "/notes/c2.md")
        completed = state.get_completed_sources()
        assert set(completed) == {"c1", "c2"}

    def test_delete_fragments_facade(self, state):
        """Facade delegates delete_fragments to fragment repo."""
        state.register_sources(["facade-del"])
        state.fragments.save_fragments("facade-del", [
            {"text": "Test", "type": "key_idea", "section": "1.1", "relevance": 3},
        ])
        deleted = state.delete_fragments("facade-del")
        assert deleted == 1
        assert state.fragments.get_fragment_stats()["total"] == 0
