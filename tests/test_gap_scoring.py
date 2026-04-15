"""Unit tests for src/klemma/api/scoring.py — pure gap scoring function.

All tests are isolated: no DB, no network, no file I/O.
"""

from __future__ import annotations

from klemma.api.scoring import (
    _compute_intent_weight,
    _compute_semantic_factor,
    _compute_top_intent,
    _cosine_similarity,
    _parse_intents,
    score_gaps,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_gap(
    hash: str = "abc",
    title: str = "Test Paper",
    count: int = 2,
    intents: str | None = None,
    avg_quality: float = 3.0,
    year: int | None = 2020,
) -> dict:
    return {
        "cited_title_hash": hash,
        "title": title,
        "authors": "Author et al.",
        "year": year,
        "count": count,
        "intents": intents,
        "avg_quality": avg_quality,
    }


# ---------------------------------------------------------------------------
# _parse_intents
# ---------------------------------------------------------------------------


def test_parse_intents_valid():
    result = _parse_intents("background,method,background")
    # both present (duplicates kept for avg)
    assert "background" in result
    assert "method" in result


def test_parse_intents_filters_invalid():
    result = _parse_intents("background,garbage_intent,method")
    assert "garbage_intent" not in result
    assert "background" in result
    assert "method" in result


def test_parse_intents_empty():
    assert _parse_intents("") == []
    assert _parse_intents(None) == []


# ---------------------------------------------------------------------------
# _compute_intent_weight
# ---------------------------------------------------------------------------


def test_intent_weight_method_beats_background():
    """method intent should produce a higher weight than background."""
    method_weight = _compute_intent_weight(["method"])
    background_weight = _compute_intent_weight(["background"])
    assert method_weight > background_weight


def test_intent_weight_extends_beats_background():
    extends_weight = _compute_intent_weight(["extends"])
    background_weight = _compute_intent_weight(["background"])
    assert extends_weight > background_weight


def test_intent_weight_empty_is_neutral():
    assert _compute_intent_weight([]) == 1.0


def test_intent_weight_avg_of_multiple():
    # method=3.0, background=1.0 → avg = 2.0
    weight = _compute_intent_weight(["method", "background"])
    assert abs(weight - 2.0) < 0.001


def test_null_intent_treated_as_one():
    """NULL intents (filtered by _parse_intents) → weight 1.0 neutral."""
    # If all intents are invalid/null, weight is neutral
    weight = _compute_intent_weight([])  # no valid intents
    assert weight == 1.0


# ---------------------------------------------------------------------------
# _compute_top_intent
# ---------------------------------------------------------------------------


def test_top_intent_by_frequency():
    intents = ["background", "method", "background"]
    top = _compute_top_intent(intents)
    assert top == "background"  # most frequent


def test_top_intent_tie_broken_by_weight():
    # method(3.0) vs background(1.0), both appear once → method wins by weight
    intents = ["method", "background"]
    top = _compute_top_intent(intents)
    assert top == "method"


def test_top_intent_empty_returns_none():
    assert _compute_top_intent([]) is None


# ---------------------------------------------------------------------------
# _cosine_similarity
# ---------------------------------------------------------------------------


def test_cosine_similarity_identical():
    v = [1.0, 0.0, 0.0]
    assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6


def test_cosine_similarity_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert abs(_cosine_similarity(a, b)) < 1e-6


def test_cosine_similarity_zero_vector():
    assert _cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


# ---------------------------------------------------------------------------
# _compute_semantic_factor
# ---------------------------------------------------------------------------


def test_semantic_factor_full_relevance():
    """Cosine=1 → factor≈1.0."""
    centroid = [1.0, 0.0]
    embeddings = {"p1": [1.0, 0.0], "p2": [1.0, 0.0]}
    factor = _compute_semantic_factor(["p1", "p2"], embeddings, {"s1": centroid})
    assert abs(factor - 1.0) < 0.01


def test_semantic_factor_zero_relevance():
    """Cosine=0 → factor=0.5 (halves the score)."""
    centroid = [1.0, 0.0]
    embeddings = {"p1": [0.0, 1.0], "p2": [0.0, 1.0]}
    factor = _compute_semantic_factor(["p1", "p2"], embeddings, {"s1": centroid})
    assert abs(factor - 0.5) < 0.01


def test_semantic_factor_no_centroids_nop():
    """No centroids → factor=1.0 (neutral)."""
    embeddings = {"p1": [1.0, 0.0], "p2": [1.0, 0.0]}
    factor = _compute_semantic_factor(["p1", "p2"], embeddings, {})
    assert factor == 1.0


def test_semantic_factor_one_citing_paper():
    """<2 citing papers with embeddings → factor=1.0 (no penalty)."""
    centroid = [1.0, 0.0]
    embeddings = {"p1": [0.0, 1.0]}  # only 1 paper
    factor = _compute_semantic_factor(["p1"], embeddings, {"s1": centroid})
    assert factor == 1.0


def test_semantic_factor_no_embeddings_nop():
    """No embeddings for citing papers → factor=1.0."""
    centroid = [1.0, 0.0]
    factor = _compute_semantic_factor(["p1", "p2"], {}, {"s1": centroid})
    assert factor == 1.0


def test_semantic_factor_range():
    """Factor must always be in [0.5, 1.0]."""
    import random
    rng = random.Random(42)
    centroids = {"s1": [rng.random() for _ in range(8)]}
    embeddings = {f"p{i}": [rng.random() for _ in range(8)] for i in range(5)}
    factor = _compute_semantic_factor(list(embeddings), embeddings, centroids)
    assert 0.5 <= factor <= 1.0


# ---------------------------------------------------------------------------
# score_gaps — integration
# ---------------------------------------------------------------------------


def test_combined_product_of_factors():
    """score = count × avg_quality × intent_weight × semantic_factor."""
    gaps = [make_gap(hash="h1", count=2, intents="method", avg_quality=4.0)]
    # No embeddings → semantic_factor=1.0
    result = score_gaps(
        gaps,
        citing_paper_ids_by_gap={"h1": ["p1", "p2"]},
        citing_embeddings={},
        section_centroids={},
        sections_by_citing_paper={},
    )
    assert len(result) == 1
    g = result[0]
    expected = 2 * 4.0 * 3.0 * 1.0  # method weight=3.0
    assert abs(g["score"] - expected) < 0.01


def test_avg_quality_boosts_score():
    gaps = [
        make_gap(hash="h1", count=3, intents="background", avg_quality=5.0),
        make_gap(hash="h2", count=3, intents="background", avg_quality=1.0),
    ]
    result = score_gaps(
        gaps,
        citing_paper_ids_by_gap={"h1": ["p1", "p2"], "h2": ["p3", "p4"]},
        citing_embeddings={},
        section_centroids={},
        sections_by_citing_paper={},
    )
    high_q = next(g for g in result if g["cited_title_hash"] == "h1")
    low_q = next(g for g in result if g["cited_title_hash"] == "h2")
    assert high_q["score"] > low_q["score"]


def test_intent_weight_method_gap_ranked_higher():
    """Same count, different intents: method > background."""
    gaps = [
        make_gap(hash="h1", count=2, intents="background"),
        make_gap(hash="h2", count=2, intents="method"),
    ]
    result = score_gaps(
        gaps,
        citing_paper_ids_by_gap={"h1": ["p1", "p2"], "h2": ["p3", "p4"]},
        citing_embeddings={},
        section_centroids={},
        sections_by_citing_paper={},
    )
    # Sorted score DESC → method gap should come first
    assert result[0]["cited_title_hash"] == "h2"


def test_sections_served_aggregation():
    """sections_served counts per section across citing papers."""
    gaps = [make_gap(hash="h1", count=3, intents=None)]
    result = score_gaps(
        gaps,
        citing_paper_ids_by_gap={"h1": ["p1", "p2", "p3"]},
        citing_embeddings={},
        section_centroids={},
        sections_by_citing_paper={
            "p1": {"1.1", "2.1"},
            "p2": {"1.1"},
            "p3": {"3.1"},
        },
    )
    g = result[0]
    sections_map = {s["section"]: s["count"] for s in g["sections_served"]}
    assert sections_map["1.1"] == 2
    assert sections_map["2.1"] == 1
    assert sections_map["3.1"] == 1


def test_invalid_intent_ignored():
    """Invalid intent strings do not break scoring."""
    gaps = [make_gap(hash="h1", count=1, intents="garbage_intent,method")]
    result = score_gaps(
        gaps,
        citing_paper_ids_by_gap={"h1": ["p1", "p2"]},
        citing_embeddings={},
        section_centroids={},
        sections_by_citing_paper={},
    )
    g = result[0]
    # Only valid intents in intents list
    assert "garbage_intent" not in g["intents"]
    # Score still computed (method has weight 3.0)
    assert g["score"] > 0


def test_sorted_by_score_desc():
    """Return value is sorted by score descending."""
    gaps = [
        make_gap(hash="h1", count=1, intents="background", avg_quality=3.0),
        make_gap(hash="h2", count=5, intents="method", avg_quality=5.0),
        make_gap(hash="h3", count=2, intents="extends", avg_quality=4.0),
    ]
    result = score_gaps(
        gaps,
        citing_paper_ids_by_gap={g["cited_title_hash"]: [f"p{i}a", f"p{i}b"] for i, g in enumerate(gaps)},
        citing_embeddings={},
        section_centroids={},
        sections_by_citing_paper={},
    )
    scores = [g["score"] for g in result]
    assert scores == sorted(scores, reverse=True)


def test_cited_by_count_alias():
    """cited_by_count alias must equal count."""
    gaps = [make_gap(hash="h1", count=7)]
    result = score_gaps(gaps, {"h1": ["p1", "p2"]}, {}, {}, {})
    assert result[0]["cited_by_count"] == 7


def test_semantic_rerank_changes_order():
    """With relevant embeddings, semantic_factor can change ranking."""
    centroid = [1.0, 0.0]
    # Gap h1: citing papers are aligned with centroid → high semantic_factor
    # Gap h2: citing papers are orthogonal → low semantic_factor (0.5)
    gaps = [
        make_gap(hash="h1", count=2, intents="background", avg_quality=3.0),
        make_gap(hash="h2", count=2, intents="background", avg_quality=3.0),
    ]
    # Without embeddings: tied score (same count × quality × intent_weight × 1.0)
    result_no_emb = score_gaps(gaps, {"h1": ["p1", "p2"], "h2": ["p3", "p4"]}, {}, {}, {})
    assert result_no_emb[0]["score"] == result_no_emb[1]["score"]

    # With embeddings: h1 citing papers align with centroid → factor≈1.0
    # h2 citing papers are orthogonal → factor=0.5 → score halved
    embeddings = {
        "p1": [1.0, 0.0], "p2": [1.0, 0.0],  # aligned
        "p3": [0.0, 1.0], "p4": [0.0, 1.0],  # orthogonal
    }
    result_with_emb = score_gaps(
        gaps,
        {"h1": ["p1", "p2"], "h2": ["p3", "p4"]},
        embeddings,
        {"s1": centroid},
        {},
    )
    h1 = next(g for g in result_with_emb if g["cited_title_hash"] == "h1")
    h2 = next(g for g in result_with_emb if g["cited_title_hash"] == "h2")
    assert h1["score"] > h2["score"]
