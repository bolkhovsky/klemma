"""Embedding providers for semantic search and scoring.

Protocol-based abstraction supporting multiple backends:
- SemanticScholar: Free S2 API (768-dim SPECTER)
- Local SPECTER: sentence-transformers allenai/specter2
- OpenAI: text-embedding-3-small (1536-dim)
- LiteLLM: any LiteLLM-compatible model (Ollama/BGE-M3, Voyage, Cohere, …)

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

        headers = {}
        if self._api_key:
            headers["x-api-key"] = self._api_key

        for attempt in range(4):
            self._wait_throttle()
            try:
                resp = requests.get(
                    "https://api.semanticscholar.org/graph/v1/paper/search",
                    params={"query": title, "fields": "embedding", "limit": 1},
                    headers=headers,
                    timeout=15,
                )
                self._last_request = time.time()
                if resp.status_code == 429:
                    wait = 15 * (2 ** attempt)  # 15, 30, 60, 120s
                    logger.debug("S2 rate-limited, retrying in %ds", wait)
                    time.sleep(wait)
                    continue
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
        logger.warning("S2 rate-limited for '%s' after retries", title[:60])
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
# LiteLLM Embeddings (any provider/model via litellm.embedding())
# ---------------------------------------------------------------------------


def _derive_embedding_provider(model: str) -> str:
    """Extract provider name from a LiteLLM embedding model string.

    Mirrors `config._derive_provider` but lives here to avoid a circular
    import (`embeddings.py` is loaded before `config.py` fully initialises).

    Examples:
        "ollama/bge-m3" → "ollama"
        "voyage/voyage-3-large" → "voyage"
        "cohere/embed-multilingual-v3.0" → "cohere"
        "openai/text-embedding-3-small" → "openai"
        "text-embedding-3-small" (bare) → "openai"
    """
    if "/" in model:
        return model.split("/", 1)[0]
    return "openai"


class LiteLLMEmbeddings:
    """Embedding provider that delegates to ``litellm.embedding()``.

    Single adapter for every LiteLLM-compatible embedding endpoint:
    Ollama (local), Voyage, Cohere, OpenAI, … Model format is
    ``provider/model`` (e.g. ``ollama/bge-m3``); bare strings are treated
    as OpenAI models by ``_derive_embedding_provider``.

    Dimension is auto-detected on the first successful call (or taken from
    the optional ``dim`` kwarg before then).
    """

    dim: int = 0  # populated on first successful embed() or from kwarg
    model_name: str = ""

    def __init__(
        self,
        model: str,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 60,
        dim: Optional[int] = None,
    ):
        try:
            import litellm as _litellm
        except ImportError:
            raise ImportError(
                "LiteLLM embedding backend requires the 'litellm' package. "
                "Install with: pip install klemma[litellm]"
            )
        self._litellm = _litellm
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.timeout = timeout
        if dim is not None:
            self.dim = dim
        # model_name: "provider/model" → "model-provider" for uniqueness
        # across providers offering the same model id.
        if "/" in model:
            provider, name = model.split("/", 1)
            self.model_name = f"{name}-{provider}"
        else:
            self.model_name = model

    def embed(self, title: str, abstract: str = "") -> Optional[list[float]]:
        """Embed a paper via ``litellm.embedding()``.

        Text format is ``f"{title}\\n{abstract}".strip()`` — no ``[SEP]``
        token, since BGE-M3 and most modern embedding models work on
        natural-language strings directly.
        """
        text = f"{title}\n{abstract}".strip()
        if not text:
            return None
        try:
            response = self._litellm.embedding(
                model=self.model,
                input=[text],
                api_base=self.api_base,
                api_key=self.api_key,
                timeout=self.timeout,
            )
            vec = response.data[0]["embedding"]
            if not vec:
                logger.warning("LiteLLM embed returned empty vector for '%s'", title[:60])
                return None
            self.dim = len(vec)
            return list(vec)
        except Exception as e:
            logger.warning("LiteLLM embed error for '%s': %s", title[:60], e)
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
        backend: "s2" | "local" | "openai" | "litellm" (default: "s2")
        model: model name/id (backend-specific; litellm: "provider/model")
        api_key_env: env var for API key (optional)
        base_url: custom endpoint (OpenAI or LiteLLM/Ollama)
        throttle: seconds between S2 requests (default: 3.1)
        timeout: request timeout in seconds (litellm only, default: 60)
        dim: explicit dimension override (litellm only; otherwise auto-detected)

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

    if backend == "litellm":
        model = config.get("model", "ollama/bge-m3")
        provider = _derive_embedding_provider(model)
        api_key = api_keys.get(provider)
        if not api_key:
            env_var = config.get("api_key_env")
            if env_var:
                api_key = os.environ.get(env_var)
        # Pure-local providers (ollama) accept api_key=None; LiteLLM handles it.
        return LiteLLMEmbeddings(
            model=model,
            api_base=config.get("base_url"),
            api_key=api_key,
            timeout=int(config.get("timeout", 60)),
            dim=config.get("dim"),
        )

    logger.warning("Unknown embedding backend: %s", backend)
    return None
