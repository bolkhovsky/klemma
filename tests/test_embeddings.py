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
