"""Embedding providers for semantic search and scoring.

Protocol-based abstraction supporting multiple backends:
- SemanticScholar: Free S2 API (768-dim SPECTER)
- Local SPECTER: sentence-transformers allenai/specter2
- OpenAI: text-embedding-3-small (1536-dim)

Install optional deps: pip install klemma[embeddings]
"""

import hashlib
import logging
import os
import time
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Contract for embedding backends."""

    dim: int
    model_name: str

    def embed(self, title: str, abstract: str = "") -> Optional[list[float]]:
        """Embed a paper by title + abstract. Returns vector or None on failure."""
        ...


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Uses pure Python to avoid mandatory numpy dependency.
    Returns 0.0 for zero-length or mismatched vectors.
    """
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _title_hash(title: str) -> str:
    """Normalize and hash a title for deduplication."""
    return hashlib.md5(title.lower().strip().encode()).hexdigest()


# ---------------------------------------------------------------------------
# Semantic Scholar Embeddings (free, 768-dim SPECTER)
# ---------------------------------------------------------------------------


class SemanticScholarEmbeddings:
    """Embedding provider using Semantic Scholar API.

    Free tier: 100 requests per 5 minutes (unauthenticated).
    Uses SPECTER paper embeddings (768 dimensions).
    """

    dim: int = 768
    model_name: str = "specter-s2"

    def __init__(self, api_key: Optional[str] = None, throttle: float = 3.1):
        self._api_key = api_key or os.environ.get("S2_API_KEY")
        self._throttle = throttle  # seconds between requests
        self._last_request = 0.0

    def _wait_throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._throttle:
            time.sleep(self._throttle - elapsed)

    def embed(self, title: str, abstract: str = "") -> Optional[list[float]]:
        """Fetch SPECTER embedding from S2 API by paper title search."""
        import requests

        self._wait_throttle()
        headers = {}
        if self._api_key:
            headers["x-api-key"] = self._api_key

        try:
            resp = requests.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={"query": title, "fields": "embedding", "limit": 1},
                headers=headers,
                timeout=15,
            )
            self._last_request = time.time()
            resp.raise_for_status()
            data = resp.json()
            papers = data.get("data", [])
            if not papers:
                logger.debug("S2: no results for '%s'", title[:60])
                return None
            emb = papers[0].get("embedding", {})
            vector = emb.get("vector")
            if vector and len(vector) == self.dim:
                return vector
            logger.debug("S2: no embedding for '%s'", title[:60])
            return None
        except Exception as e:
            logger.warning("S2 API error for '%s': %s", title[:60], e)
            return None


# ---------------------------------------------------------------------------
# Local SPECTER (sentence-transformers)
# ---------------------------------------------------------------------------


class LocalSPECTEREmbeddings:
    """Local SPECTER2 embeddings via sentence-transformers.

    Requires: pip install klemma[local-embeddings]
    Model: allenai/specter2 (768 dimensions)
    """

    dim: int = 768
    model_name: str = "specter2-local"

    def __init__(self, model_id: str = "allenai/specter2"):
        self._model_id = model_id
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_id)
            logger.info("Loaded local model: %s", self._model_id)
        except ImportError:
            raise ImportError(
                "Install klemma[local-embeddings] for local SPECTER: "
                "pip install klemma[local-embeddings]"
            )

    def embed(self, title: str, abstract: str = "") -> Optional[list[float]]:
        """Embed locally using SPECTER2."""
        self._load_model()
        text = f"{title} [SEP] {abstract}" if abstract else title
        try:
            vec = self._model.encode(text, show_progress_bar=False)
            return vec.tolist()
        except Exception as e:
            logger.warning("Local embed error: %s", e)
            return None


# ---------------------------------------------------------------------------
# OpenAI Embeddings (text-embedding-3-small, 1536-dim)
# ---------------------------------------------------------------------------


class OpenAIEmbeddings:
    """OpenAI text-embedding-3-small (1536 dimensions).

    Requires: pip install klemma[openai]
    """

    dim: int = 1536
    model_name: str = "text-embedding-3-small"

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key_env: str = "OPENAI_API_KEY",
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.model_name = model
        self._api_key_env = api_key_env
        self._base_url = base_url
        self._api_key = api_key  # direct key takes priority over env var

    def embed(self, title: str, abstract: str = "") -> Optional[list[float]]:
        """Embed using OpenAI API."""
        try:
            import openai
        except ImportError:
            raise ImportError(
                "Install klemma[openai] for OpenAI embeddings: "
                "pip install klemma[openai]"
            )
        text = f"{title}. {abstract}" if abstract else title
        api_key = self._api_key or os.environ.get(self._api_key_env)
        if not api_key:
            logger.warning("No API key found in %s", self._api_key_env)
            return None
        try:
            client = openai.OpenAI(api_key=api_key, base_url=self._base_url)
            resp = client.embeddings.create(input=text, model=self.model_name)
            vec = resp.data[0].embedding
            self.dim = len(vec)
            return vec
        except Exception as e:
            logger.warning("OpenAI embed error: %s", e)
            return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_embeddings(
    config: dict,
    api_keys: Optional[dict] = None,
) -> Optional[EmbeddingProvider]:
    """Create an EmbeddingProvider from config dict.

    Config keys:
        backend: "s2" | "local" | "openai" (default: "s2")
        model: model name/id (optional, backend-specific)
        api_key_env: env var for API key (optional)
        base_url: custom endpoint (OpenAI only)
        throttle: seconds between S2 requests (default: 3.1)

    api_keys: optional dict from klemmarc (e.g. {"openai": "sk-..."}).
    Direct keys take priority over env vars.

    Returns None if backend is disabled or misconfigured.
    """
    if not config:
        return None

    api_keys = api_keys or {}
    backend = config.get("backend", "s2")

    if backend == "s2":
        api_key = api_keys.get("s2")
        if not api_key:
            env_var = config.get("api_key_env", "S2_API_KEY")
            if env_var:
                api_key = os.environ.get(env_var)
        throttle = config.get("throttle", 3.1)
        return SemanticScholarEmbeddings(api_key=api_key, throttle=throttle)

    if backend == "local":
        model_id = config.get("model", "allenai/specter2")
        return LocalSPECTEREmbeddings(model_id=model_id)

    if backend == "openai":
        return OpenAIEmbeddings(
            model=config.get("model", "text-embedding-3-small"),
            api_key_env=config.get("api_key_env", "OPENAI_API_KEY"),
            base_url=config.get("base_url"),
            api_key=api_keys.get("openai"),
        )

    logger.warning("Unknown embedding backend: %s", backend)
    return None
