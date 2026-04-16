"""Tests for citation intent handling in the bibliography extraction pipeline.

Focuses on save_citation_links validation and the fallback inline bibliography
path (which must NOT request citation_intent — hallucination risk from bib-only text).
All tests are isolated: no real AI calls, no network, no file I/O.
"""

from __future__ import annotations

import hashlib

import pytest

from klemma.stores.paper_store import LocalPaperStore


@pytest.fixture
def paper_store(tmp_path):
    return LocalPaperStore(tmp_path / "library.db")


@pytest.fixture
def paper_id(paper_store):
    return paper_store.register_paper(title="Test Paper", pdf_hash="abc123")


def _get_intent(paper_store: LocalPaperStore, paper_id: str, ref_title: str) -> str | None:
    """Read citation_intent from citation_graph for a specific gap."""
    title_hash = hashlib.md5(ref_title.lower().encode()).hexdigest()
    with paper_store._conn() as conn:
        row = conn.execute(
            "SELECT citation_intent FROM citation_graph WHERE citing_paper_id=? AND cited_title_hash=?",
            (paper_id, title_hash),
        ).fetchone()
    return row["citation_intent"] if row else None


def _get_all_intents(paper_store: LocalPaperStore, paper_id: str) -> dict[str, str | None]:
    """Return {cited_title: citation_intent} for all citation_graph entries of a paper."""
    with paper_store._conn() as conn:
        rows = conn.execute(
            "SELECT cited_title, citation_intent FROM citation_graph WHERE citing_paper_id=?",
            (paper_id,),
        ).fetchall()
    return {r["cited_title"]: r["citation_intent"] for r in rows}


# ---------------------------------------------------------------------------
# Valid intent values are saved as-is
# ---------------------------------------------------------------------------


def test_valid_method_intent_saved(paper_store, paper_id):
    """citation_intent='method' is stored verbatim in citation_graph."""
    paper_store.save_citation_links(paper_id, [
        {"title": "Method Paper", "authors": "A", "year": 2020, "citation_intent": "method"},
    ])
    assert _get_intent(paper_store, paper_id, "Method Paper") == "method"


def test_valid_extends_intent_saved(paper_store, paper_id):
    paper_store.save_citation_links(paper_id, [
        {"title": "Extends Paper", "authors": "B", "year": 2021, "citation_intent": "extends"},
    ])
    assert _get_intent(paper_store, paper_id, "Extends Paper") == "extends"


def test_all_valid_intents_saved(paper_store, paper_id):
    """All 6 valid intent values are stored correctly."""
    valid = ["background", "method", "result_comparison", "extends", "contrasts", "uses_data"]
    refs = [
        {"title": f"Paper {intent}", "authors": "X", "year": 2020, "citation_intent": intent}
        for intent in valid
    ]
    paper_store.save_citation_links(paper_id, refs)
    intents = _get_all_intents(paper_store, paper_id)
    for intent in valid:
        assert intents[f"Paper {intent}"] == intent


# ---------------------------------------------------------------------------
# Null / missing intent → NULL (not "background")
# ---------------------------------------------------------------------------


def test_null_intent_stored_as_null(paper_store, paper_id):
    """citation_intent: null from AI → stored as NULL (not 'background')."""
    paper_store.save_citation_links(paper_id, [
        {"title": "Null Intent Paper", "authors": "A", "year": 2020, "citation_intent": None},
    ])
    assert _get_intent(paper_store, paper_id, "Null Intent Paper") is None


def test_missing_intent_key_stored_as_null(paper_store, paper_id):
    """Ref dict with no citation_intent key → stored as NULL (fallback inline path)."""
    paper_store.save_citation_links(paper_id, [
        {"title": "Bib Only Paper", "authors": "B", "year": 2019},
    ])
    assert _get_intent(paper_store, paper_id, "Bib Only Paper") is None


def test_fallback_inline_path_all_null(paper_store, paper_id):
    """Inline bibliography fallback refs (no citation_intent field) → all NULL intents.

    The fallback prompt only asks for title/authors/year — no intent extraction.
    This test simulates what save_citation_links receives from that path.
    """
    refs = [
        {"title": f"Bib Ref {i}", "authors": "Auth", "year": 2018}
        for i in range(5)
    ]
    paper_store.save_citation_links(paper_id, refs)
    intents = _get_all_intents(paper_store, paper_id)
    assert all(v is None for v in intents.values()), (
        "All inline-bibliography refs must have NULL intent (not guessed from title)"
    )


# ---------------------------------------------------------------------------
# Invalid intent → NULL (not silently stored)
# ---------------------------------------------------------------------------


def test_invalid_intent_garbage_stored_as_null(paper_store, paper_id):
    """citation_intent='garbage_intent' → stored as NULL, not saved verbatim."""
    paper_store.save_citation_links(paper_id, [
        {"title": "Invalid Intent Paper", "authors": "X", "year": 2020, "citation_intent": "garbage_intent"},
    ])
    assert _get_intent(paper_store, paper_id, "Invalid Intent Paper") is None


def test_invalid_intent_does_not_store_background_default(paper_store, paper_id):
    """Regression: invalid intent must NOT silently default to 'background'."""
    paper_store.save_citation_links(paper_id, [
        {"title": "No Default Paper", "authors": "Y", "year": 2020, "citation_intent": "unknown_value"},
    ])
    intent = _get_intent(paper_store, paper_id, "No Default Paper")
    assert intent != "background", (
        "Invalid intent must be NULL, not 'background' — the hardcoded default was removed"
    )


def test_empty_string_intent_stored_as_null(paper_store, paper_id):
    """Empty string citation_intent → stored as NULL."""
    paper_store.save_citation_links(paper_id, [
        {"title": "Empty Intent Paper", "authors": "Z", "year": 2020, "citation_intent": ""},
    ])
    assert _get_intent(paper_store, paper_id, "Empty Intent Paper") is None


# ---------------------------------------------------------------------------
# Mixed intents in one batch
# ---------------------------------------------------------------------------


def test_mixed_batch_valid_and_invalid(paper_store, paper_id):
    """Batch with valid + invalid intents: valid saved, invalid → NULL."""
    paper_store.save_citation_links(paper_id, [
        {"title": "Good Paper", "authors": "A", "year": 2020, "citation_intent": "method"},
        {"title": "Bad Paper", "authors": "B", "year": 2020, "citation_intent": "not_a_real_intent"},
        {"title": "Null Paper", "authors": "C", "year": 2020, "citation_intent": None},
    ])
    assert _get_intent(paper_store, paper_id, "Good Paper") == "method"
    assert _get_intent(paper_store, paper_id, "Bad Paper") is None
    assert _get_intent(paper_store, paper_id, "Null Paper") is None
