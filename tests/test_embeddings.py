"""Tests for embedding providers and utilities."""

import pytest

from klemma.embeddings import (
    EmbeddingProvider,
    LocalSPECTEREmbeddings,
    OpenAIEmbeddings,
    SemanticScholarEmbeddings,
    cosine_similarity,
    create_embeddings,
)
from klemma.state import StateManager


class TestCosigneSimilarity:
    """Tests for cosine_similarity utility."""

    def test_identical_vectors(self):
        assert cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_similar_vectors(self):
        sim = cosine_similarity([1, 1, 0], [1, 0, 0])
        assert 0.5 < sim < 1.0

    def test_zero_vector(self):
        assert cosine_similarity([0, 0, 0], [1, 2, 3]) == 0.0

    def test_empty_vectors(self):
        assert cosine_similarity([], []) == 0.0

    def test_mismatched_lengths(self):
        assert cosine_similarity([1, 2], [1, 2, 3]) == 0.0


class TestCreateEmbeddings:
    """Tests for factory function."""

    def test_none_config(self):
        assert create_embeddings(None) is None

    def test_empty_config(self):
        assert create_embeddings({}) is None

    def test_s2_backend(self):
        provider = create_embeddings({"backend": "s2"})
        assert provider is not None
        assert isinstance(provider, SemanticScholarEmbeddings)
        assert provider.dim == 768
        assert provider.model_name == "specter-s2"

    def test_local_backend(self):
        provider = create_embeddings({"backend": "local"})
        assert provider is not None
        assert isinstance(provider, LocalSPECTEREmbeddings)
        assert provider.dim == 768

    def test_openai_backend(self):
        provider = create_embeddings({"backend": "openai"})
        assert provider is not None
        assert isinstance(provider, OpenAIEmbeddings)
        assert provider.dim == 1536

    def test_unknown_backend(self):
        assert create_embeddings({"backend": "unknown"}) is None

    def test_custom_throttle_s2(self):
        provider = create_embeddings({"backend": "s2", "throttle": 5.0})
        assert provider._throttle == 5.0

    def test_custom_model_openai(self):
        provider = create_embeddings({
            "backend": "openai",
            "model": "text-embedding-ada-002",
        })
        assert provider.model_name == "text-embedding-ada-002"


class TestProtocolCompliance:
    """Verify all providers satisfy EmbeddingProvider protocol."""

    def test_s2_is_provider(self):
        p = SemanticScholarEmbeddings()
        assert isinstance(p, EmbeddingProvider)

    def test_local_is_provider(self):
        p = LocalSPECTEREmbeddings()
        assert isinstance(p, EmbeddingProvider)

    def test_openai_is_provider(self):
        p = OpenAIEmbeddings()
        assert isinstance(p, EmbeddingProvider)


class TestSemanticScholarThrottle:
    """Test S2 rate limiting."""

    def test_throttle_default(self):
        p = SemanticScholarEmbeddings()
        assert p._throttle == 3.1

    def test_throttle_custom(self):
        p = SemanticScholarEmbeddings(throttle=1.0)
        assert p._throttle == 1.0


class TestMockEmbedding:
    """Test with a mock provider to verify protocol usage patterns."""

    class MockProvider:
        dim: int = 3
        model_name: str = "mock"

        def embed(self, title, abstract=""):
            return [0.1, 0.2, 0.3]

    def test_mock_is_provider(self):
        p = self.MockProvider()
        assert isinstance(p, EmbeddingProvider)

    def test_mock_embed(self):
        p = self.MockProvider()
        vec = p.embed("test paper")
        assert len(vec) == 3

    def test_mock_similarity(self):
        p = self.MockProvider()
        a = p.embed("paper A")
        b = p.embed("paper B")
        assert cosine_similarity(a, b) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Embedding Storage (StateManager integration)
# ---------------------------------------------------------------------------


@pytest.fixture
def state(tmp_path):
    """Create a StateManager with a temporary database."""
    db_path = tmp_path / "test.db"
    return StateManager(db_path)


class TestEmbeddingStorage:
    """Tests for save/get embedding roundtrip in StateManager."""

    def test_save_and_get_roundtrip(self, state):
        """Save embedding, retrieve it — vectors match."""
        state.register_sources(["src1"])
        vec = [0.1, 0.2, 0.3, 0.4]
        state.save_embedding("src1", vec, "test-model")
        result = state.get_embedding("src1")
        assert result is not None
        retrieved_vec, model = result
        assert model == "test-model"
        assert len(retrieved_vec) == 4
        for a, b in zip(vec, retrieved_vec):
            assert a == pytest.approx(b, abs=1e-6)

    def test_get_embedding_nonexistent(self, state):
        """Non-existent source returns None."""
        assert state.get_embedding("nonexistent") is None

    def test_get_embedding_no_embedding(self, state):
        """Source without embedding returns None."""
        state.register_sources(["src1"])
        assert state.get_embedding("src1") is None

    def test_768_dim_roundtrip(self, state):
        """Roundtrip with SPECTER-sized 768-dim vector."""
        state.register_sources(["src1"])
        vec = [float(i) / 768 for i in range(768)]
        state.save_embedding("src1", vec, "specter-s2")
        result = state.get_embedding("src1")
        assert result is not None
        retrieved_vec, model = result
        assert len(retrieved_vec) == 768
        assert model == "specter-s2"

    def test_overwrite_embedding(self, state):
        """Saving again overwrites the previous embedding."""
        state.register_sources(["src1"])
        state.save_embedding("src1", [1.0, 2.0], "model-a")
        state.save_embedding("src1", [3.0, 4.0, 5.0], "model-b")
        result = state.get_embedding("src1")
        vec, model = result
        assert len(vec) == 3
        assert model == "model-b"

    def test_get_all_embeddings(self, state):
        """Get all embeddings across sources."""
        state.register_sources(["src1", "src2", "src3"])
        state.save_embedding("src1", [1.0, 0.0], "m1")
        state.save_embedding("src2", [0.0, 1.0], "m1")
        # src3 has no embedding
        all_emb = state.get_all_embeddings()
        assert len(all_emb) == 2
        assert "src1" in all_emb
        assert "src2" in all_emb
        assert "src3" not in all_emb

    def test_get_all_embeddings_filtered_by_model(self, state):
        """Filter by model name."""
        state.register_sources(["src1", "src2"])
        state.save_embedding("src1", [1.0], "specter")
        state.save_embedding("src2", [2.0], "openai")
        specter_only = state.get_all_embeddings(model="specter")
        assert len(specter_only) == 1
        assert "src1" in specter_only

    def test_embedding_stats(self, state):
        """Get embedding coverage stats."""
        state.register_sources(["src1", "src2", "src3"])
        # Mark some as completed
        with state._conn() as conn:
            conn.execute(
                "UPDATE sources SET status='completed' WHERE id IN ('src1','src2','src3')"
            )
        state.save_embedding("src1", [1.0], "specter")
        state.save_embedding("src2", [2.0], "openai")
        stats = state.get_embedding_stats()
        assert stats["total"] == 3
        assert stats["embedded"] == 2
        assert stats["models"]["specter"] == 1
        assert stats["models"]["openai"] == 1


class TestMigrationV2:
    """Test schema migration to version 2."""

    def test_schema_version_is_two(self, state):
        with state._conn() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 2

    def test_sources_has_embedding_columns(self, state):
        with state._conn() as conn:
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(sources)")
            }
        assert "embedding" in cols
        assert "embedding_model" in cols
