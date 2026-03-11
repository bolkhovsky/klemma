"""Content-addressable hashing utilities for the three-tier library (ADR-014)."""

import hashlib
from pathlib import Path


def compute_pdf_hash(pdf_path: Path) -> str:
    """SHA256 of PDF bytes — used for content-addressable paper dedup."""
    return hashlib.sha256(pdf_path.read_bytes()).hexdigest()


def compute_content_hash(paper_id: str, text: str, page: int | None) -> str:
    """Content-addressable fragment ID: SHA256(paper_id + text + page).

    Deterministic — same PDF + same extraction = same fragment IDs.
    """
    content = f"{paper_id}:{text}:{page or 0}"
    return hashlib.sha256(content.encode()).hexdigest()


def compute_prompt_hash(prompt_text: str) -> str:
    """Hash of extraction prompt for versioning (first 16 hex chars)."""
    return hashlib.sha256(prompt_text.encode()).hexdigest()[:16]
