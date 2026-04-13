"""Tests for embedding providers and utilities."""

from unittest.mock import MagicMock

import pytest

from klemma.embeddings import (
    EmbeddingProvider,
    LiteLLMEmbeddings,
    LocalSPECTEREmbeddings,
    OpenAIEmbeddings,
    SemanticScholarEmbeddings,
    _derive_embedding_provider,
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

    def test_litellm_backend(self):
        provider = create_embeddings({
            "backend": "litellm",
            "model": "ollama/bge-m3",
            "base_url": "http://localhost:11434",
        })
        assert provider is not None
        assert isinstance(provider, LiteLLMEmbeddings)
        assert provider.model == "ollama/bge-m3"
        assert provider.api_base == "http://localhost:11434"
        assert provider.model_name == "bge-m3-ollama"

    def test_litellm_backend_default_model(self):
        provider = create_embeddings({"backend": "litellm"})
        assert isinstance(provider, LiteLLMEmbeddings)
        assert provider.model == "ollama/bge-m3"

    def test_litellm_backend_api_key_from_dict(self):
        provider = create_embeddings(
            {"backend": "litellm", "model": "voyage/voyage-3-large"},
            api_keys={"voyage": "pa-test-key"},
        )
        assert isinstance(provider, LiteLLMEmbeddings)
        assert provider.api_key == "pa-test-key"

    def test_litellm_backend_custom_timeout(self):
        provider = create_embeddings({
            "backend": "litellm",
            "model": "ollama/bge-m3",
            "timeout": 120,
        })
        assert provider.timeout == 120

    def test_litellm_backend_explicit_dim(self):
        provider = create_embeddings({
            "backend": "litellm",
            "model": "ollama/bge-m3",
            "dim": 1024,
        })
        assert provider.dim == 1024


class TestDeriveEmbeddingProvider:
    """Tests for _derive_embedding_provider."""

    def test_ollama(self):
        assert _derive_embedding_provider("ollama/bge-m3") == "ollama"

    def test_voyage(self):
        assert _derive_embedding_provider("voyage/voyage-3-large") == "voyage"

    def test_cohere(self):
        assert _derive_embedding_provider("cohere/embed-multilingual-v3.0") == "cohere"

    def test_openai_slash(self):
        assert _derive_embedding_provider("openai/text-embedding-3-small") == "openai"

    def test_openai_bare(self):
        assert _derive_embedding_provider("text-embedding-3-small") == "openai"


class TestLiteLLMEmbeddings:
    """Tests for LiteLLMEmbeddings class (mocked litellm.embedding)."""

    def _make_response(self, vec):
        response = MagicMock()
        response.data = [{"embedding": vec}]
        return response

    def test_model_name_ollama(self):
        p = LiteLLMEmbeddings("ollama/bge-m3")
        assert p.model_name == "bge-m3-ollama"

    def test_model_name_voyage(self):
        p = LiteLLMEmbeddings("voyage/voyage-3-large")
        assert p.model_name == "voyage-3-large-voyage"

    def test_model_name_bare(self):
        p = LiteLLMEmbeddings("text-embedding-3-small")
        assert p.model_name == "text-embedding-3-small"

    def test_embed_success(self):
        p = LiteLLMEmbeddings("ollama/bge-m3", api_base="http://localhost:11434")
        p._litellm = MagicMock()
        p._litellm.embedding.return_value = self._make_response([0.1, 0.2, 0.3])
        vec = p.embed("Test title", "Test abstract")
        assert vec == [0.1, 0.2, 0.3]
        assert p.dim == 3
        call_kwargs = p._litellm.embedding.call_args.kwargs
        assert call_kwargs["model"] == "ollama/bge-m3"
        assert call_kwargs["api_base"] == "http://localhost:11434"
        assert call_kwargs["input"] == ["Test title\nTest abstract"]

    def test_embed_title_only(self):
        p = LiteLLMEmbeddings("ollama/bge-m3")
        p._litellm = MagicMock()
        p._litellm.embedding.return_value = self._make_response([0.5, 0.5])
        p.embed("Only title")
        call_kwargs = p._litellm.embedding.call_args.kwargs
        assert call_kwargs["input"] == ["Only title"]

    def test_embed_empty_input_returns_none(self):
        p = LiteLLMEmbeddings("ollama/bge-m3")
        p._litellm = MagicMock()
        assert p.embed("", "") is None
        p._litellm.embedding.assert_not_called()

    def test_embed_error_returns_none(self):
        p = LiteLLMEmbeddings("ollama/bge-m3")
        p._litellm = MagicMock()
        p._litellm.embedding.side_effect = ConnectionError("ollama down")
        assert p.embed("title") is None

    def test_embed_empty_vector_returns_none(self):
        p = LiteLLMEmbeddings("ollama/bge-m3")
        p._litellm = MagicMock()
        p._litellm.embedding.return_value = self._make_response([])
        assert p.embed("title") is None

    def test_dim_updates_on_success(self):
        p = LiteLLMEmbeddings("ollama/bge-m3", dim=999)
        p._litellm = MagicMock()
        p._litellm.embedding.return_value = self._make_response([0.0] * 1024)
        p.embed("title")
        assert p.dim == 1024

    def test_is_embedding_provider(self):
        p = LiteLLMEmbeddings("ollama/bge-m3")
        assert isinstance(p, EmbeddingProvider)

    def test_embed_batch_single_call(self):
        """embed_batch sends all non-empty texts in a single HTTP call."""
        p = LiteLLMEmbeddings("ollama/bge-m3")
        p._litellm = MagicMock()
        response = MagicMock()
        response.data = [
            {"embedding": [0.1, 0.2]},
            {"embedding": [0.3, 0.4]},
            {"embedding": [0.5, 0.6]},
        ]
        p._litellm.embedding.return_value = response

        vectors = p.embed_batch(["one", "two", "three"])

        assert p._litellm.embedding.call_count == 1
        assert vectors == [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
        assert p._litellm.embedding.call_args.kwargs["input"] == ["one", "two", "three"]

    def test_embed_batch_preserves_none_for_empty(self):
        """Empty strings become None without being sent to the backend."""
        p = LiteLLMEmbeddings("ollama/bge-m3")
        p._litellm = MagicMock()
        response = MagicMock()
        response.data = [
            {"embedding": [0.1]},
            {"embedding": [0.2]},
        ]
        p._litellm.embedding.return_value = response

        vectors = p.embed_batch(["one", "", "two", "   "])

        assert vectors == [[0.1], None, [0.2], None]
        assert p._litellm.embedding.call_args.kwargs["input"] == ["one", "two"]

    def test_embed_batch_all_empty_skips_call(self):
        p = LiteLLMEmbeddings("ollama/bge-m3")
        p._litellm = MagicMock()
        assert p.embed_batch(["", "  "]) == [None, None]
        p._litellm.embedding.assert_not_called()

    def test_embed_batch_empty_list(self):
        p = LiteLLMEmbeddings("ollama/bge-m3")
        p._litellm = MagicMock()
        assert p.embed_batch([]) == []
        p._litellm.embedding.assert_not_called()

    def test_embed_batch_error_returns_all_none(self):
        p = LiteLLMEmbeddings("ollama/bge-m3")
        p._litellm = MagicMock()
        p._litellm.embedding.side_effect = ConnectionError("ollama down")
        assert p.embed_batch(["one", "two"]) == [None, None]


class TestDefaultEmbedBatchFallback:
    """The _default_embed_batch helper used by backends without batch support."""

    def test_falls_back_to_sequential_embed(self):
        from klemma.embeddings import _default_embed_batch

        provider = MagicMock()
        provider.embed.side_effect = [[1.0], [2.0], None]

        result = _default_embed_batch(provider, ["a", "b", "c"])

        assert result == [[1.0], [2.0], None]
        assert provider.embed.call_count == 3

    def test_skips_empty_without_calling(self):
        from klemma.embeddings import _default_embed_batch

        provider = MagicMock()
        provider.embed.return_value = [0.5]

        result = _default_embed_batch(provider, ["text", ""])

        assert result == [[0.5], None]
        assert provider.embed.call_count == 1


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
    """Test schema migration includes version 2 changes."""

    def test_schema_version_at_least_two(self, state):
        with state._conn() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version >= 2

    def test_sources_has_embedding_columns(self, state):
        with state._conn() as conn:
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(sources)")
            }
        assert "embedding" in cols
        assert "embedding_model" in cols


class TestHybridDiscovery:
    """Tests for hybrid keyword+semantic discovery scoring."""

    class FakeEntry:
        def __init__(self, title="", abstract="", keywords=""):
            self.title = title
            self.abstract = abstract
            self.keywords = keywords

    class FakeEmbeddings:
        dim = 3
        model_name = "test"

        def __init__(self, vec):
            self._vec = vec

        def embed(self, title, abstract=""):
            return self._vec

    def test_keyword_only_fallback(self, tmp_path):
        """Without embeddings, keyword scoring works as before."""
        from klemma.discovery import discover_relevant_sources

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "notes").mkdir()

        entries = {
            "Smith2020": self.FakeEntry(
                title="Machine learning for embeddings",
                abstract="We propose a method",
            ),
            "Jones2019": self.FakeEntry(
                title="Unrelated paper about cooking",
                abstract="Recipes and ingredients",
            ),
        }
        results = discover_relevant_sources(
            vault, "notes", entries, keywords=["machine", "learning"]
        )
        assert len(results) >= 1
        assert results[0]["citekey"] == "Smith2020"

    def test_hybrid_adds_semantic_matches(self, tmp_path, state):
        """With embeddings, semantically close sources appear even without keyword match."""
        from klemma.discovery import discover_relevant_sources

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "notes").mkdir()

        entries = {
            "Smith2020": self.FakeEntry(
                title="Machine learning for NLP",
                abstract="We propose a method",
            ),
            "Hidden2021": self.FakeEntry(
                title="Some obscure title",
                abstract="No keyword matches here",
            ),
        }

        # Store embedding for Hidden2021 that's close to query
        state.register_sources(["Smith2020", "Hidden2021"])
        state.save_embedding("Hidden2021", [0.9, 0.1, 0.0], "test")
        state.save_embedding("Smith2020", [0.1, 0.9, 0.0], "test")

        # Query vector close to Hidden2021
        emb = self.FakeEmbeddings([0.85, 0.15, 0.0])

        results = discover_relevant_sources(
            vault, "notes", entries, keywords=["machine"],
            embeddings=emb, state=state, query_title="Machine learning"
        )
        citekeys = [r["citekey"] for r in results]
        # Hidden2021 should appear via semantic similarity
        assert "Hidden2021" in citekeys

    def test_hybrid_score_has_semantic_sim(self, tmp_path, state):
        """Hybrid results include semantic_sim field."""
        from klemma.discovery import discover_relevant_sources

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "notes").mkdir()

        entries = {
            "A2020": self.FakeEntry(
                title="Keyword match here",
                abstract="Contains keyword",
            ),
        }
        state.register_sources(["A2020"])
        state.save_embedding("A2020", [1.0, 0.0, 0.0], "test")
        emb = self.FakeEmbeddings([1.0, 0.0, 0.0])

        results = discover_relevant_sources(
            vault, "notes", entries, keywords=["keyword"],
            embeddings=emb, state=state, query_title="Keyword search"
        )
        assert len(results) >= 1
        assert "semantic_sim" in results[0]
        assert results[0]["semantic_sim"] > 0.9
