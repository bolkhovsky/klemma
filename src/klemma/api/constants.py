"""Shared constants for the Klemma API.

Centralised here so magic numbers in tasks.py are documented and testable.
"""

# ── Verbatim validation window ─────────────────────────────────────────────
# The verbatim validator does offline substring matching — no LLM involved —
# so it can safely work on a longer window than the AI extraction prompt.
# For small PDFs (below VERBATIM_VALIDATION_CAP_SMALL) we validate against
# the full text. For large PDFs we cap at VERBATIM_VALIDATION_CAP_LARGE as a
# pathological-input backstop only; the cap is intentionally generous.
#
# Cap rationale (#382): difflib.SequenceMatcher with autojunk=False uses
# O(len(pdf_text)) memory for the position-lookup dict — a 1 MB text fits
# in <50 MB RSS. The exact-substring fast path is CPython-optimized and
# stays sub-second on 1 MB. 1_000_000 covers every academic paper plus
# most book-length normative documents while still bounding RAM in case of
# a misuploaded multi-MB scan or OCR dump.
#
# AI extraction is chunked separately via build_chunks_from_pages — that
# limit is unrelated and lives in literature/pdf.py.
VERBATIM_VALIDATION_CAP_SMALL = 100_000     # full text used when pdf_text < this
VERBATIM_VALIDATION_CAP_LARGE = 1_000_000   # backstop for pathological inputs (#382)

# ── Embeddings enforcement ──────────────────────────────────────────────────
# SaaS must use local Ollama embeddings — no external API calls for embeddings.
# Set KLEMMA_EMBEDDINGS_ALLOW_REMOTE=1 in CI/test environments to bypass.
EMBEDDINGS_REQUIRED_BACKEND = "litellm"
EMBEDDINGS_REQUIRED_MODEL_PREFIX = "ollama/"
