"""Shared constants for the Klemma API.

Centralised here so magic numbers in tasks.py are documented and testable.
"""

# ── Verbatim validation window ─────────────────────────────────────────────
# The verbatim validator does offline substring matching — no LLM involved —
# so it can safely work on a longer window than the AI extraction prompt.
# For small PDFs (below VERBATIM_VALIDATION_CAP_SMALL) we validate against
# the full text so fragments near the bibliography aren't falsely downgraded.
# For large PDFs we cap at VERBATIM_VALIDATION_CAP_LARGE to keep peak RAM
# predictable (substring search is O(n*m) in degenerate cases).
#
# AI extraction still uses pdf_text[:50_000] — that cap is tied to LLM context
# and must not be changed here.
VERBATIM_VALIDATION_CAP_SMALL = 100_000   # full text used when pdf_text < this
VERBATIM_VALIDATION_CAP_LARGE = 150_000   # cap applied when pdf_text >= SMALL

# ── Embeddings enforcement ──────────────────────────────────────────────────
# SaaS must use local Ollama embeddings — no external API calls for embeddings.
# Set KLEMMA_EMBEDDINGS_ALLOW_REMOTE=1 in CI/test environments to bypass.
EMBEDDINGS_REQUIRED_BACKEND = "litellm"
EMBEDDINGS_REQUIRED_MODEL_PREFIX = "ollama/"
