"""Gap scoring — pure scoring function for SaaS /library/gaps endpoint.

Implements the CLI scoring formula (src/klemma/repositories/gaps.py:36-206) for
the SaaS architecture. Does NOT import from repositories/ — different DB layout.

Formula: score = count × avg_quality × intent_weight × semantic_factor

  count          — number of user papers citing this gap
  avg_quality    — AVG(COALESCE(quality_score, 3)) from user_sources (SQL-computed)
  intent_weight  — AVG of per-intent weights (Teufel 2006 citation function taxonomy):
                     method=3.0, extends=2.5, result_comparison=2.0,
                     contrasts=2.0, uses_data=1.5, background/None/unknown=1.0
  semantic_factor — 0.5 + 0.5 × max_section_cosine, range [0.5, 1.0]
                    NOISE PENALTY (not a boost): cosine=0 → 0.5 (halves score),
                    cosine=1 → 1.0 (neutral). <2 citing papers with embeddings → 1.0.

Academic foundation: Teufel et al. (2006) citation function taxonomy.
Mirrors: src/klemma/repositories/gaps.py (CLI reference implementation).
"""

from __future__ import annotations

from typing import Optional

_INTENT_WEIGHTS: dict[str, float] = {
    "method": 3.0,
    "extends": 2.5,
    "result_comparison": 2.0,
    "contrasts": 2.0,
    "uses_data": 1.5,
    "background": 1.0,
}

_VALID_INTENTS = frozenset(_INTENT_WEIGHTS)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _parse_intents(intents_str: Optional[str]) -> list[str]:
    """Parse GROUP_CONCAT intent string into list of valid intents."""
    if not intents_str:
        return []
    return [p.strip() for p in intents_str.split(",") if p.strip() in _VALID_INTENTS]


def _compute_intent_weight(intents: list[str]) -> float:
    """Compute average intent weight across all observed intents.

    Unknown/NULL intents → 1.0 (neutral). Empty list → 1.0.
    """
    if not intents:
        return 1.0
    weights = [_INTENT_WEIGHTS.get(i, 1.0) for i in intents]
    return sum(weights) / len(weights)


def _compute_top_intent(intents: list[str]) -> Optional[str]:
    """Return the most frequent (highest-weight on tie) intent."""
    if not intents:
        return None
    freq: dict[str, int] = {}
    for i in intents:
        freq[i] = freq.get(i, 0) + 1
    return max(freq, key=lambda i: (freq[i], _INTENT_WEIGHTS.get(i, 1.0)))


def _compute_semantic_factor(
    citing_paper_ids: list[str],
    paper_embeddings: dict[str, list[float]],
    section_centroids: dict[str, list[float]],
) -> float:
    """Compute semantic_factor = 0.5 + 0.5 × max_section_avg_cosine.

    semantic_factor is a NOISE PENALTY, not a boost:
    - High relevance (cosine ≈ 1) → factor ≈ 1.0 (neutral)
    - Low relevance (cosine ≈ 0) → factor ≈ 0.5 (halves the score)
    - <2 citing papers with embeddings → 1.0 (no penalty — insufficient data)

    Range: [0.5, 1.0].
    """
    if not section_centroids:
        return 1.0

    vecs = [paper_embeddings[pid] for pid in citing_paper_ids if pid in paper_embeddings]
    if not vecs:
        return 1.0

    # Filter to consistent dimensions — mixed embedding models can produce
    # vectors of different lengths, which would cause IndexError in cosine similarity.
    dim = len(vecs[0])
    vecs = [v for v in vecs if len(v) == dim]
    if len(vecs) < 2:
        return 1.0  # Not enough data — neutral

    max_avg_cosine = 0.0
    for centroid in section_centroids.values():
        cosines = [_cosine_similarity(v, centroid) for v in vecs]
        avg_cosine = sum(cosines) / len(cosines)
        if avg_cosine > max_avg_cosine:
            max_avg_cosine = avg_cosine

    return 0.5 + 0.5 * max_avg_cosine


def score_gaps(
    raw_gaps: list[dict],
    citing_paper_ids_by_gap: dict[str, list[str]],
    citing_embeddings: dict[str, list[float]],
    section_centroids: dict[str, list[float]],
    sections_by_citing_paper: dict[str, set[str]],
) -> list[dict]:
    """Score and enrich reference gaps using: count × avg_quality × intent_weight × semantic_factor.

    Args:
        raw_gaps: gap dicts from paper_store.get_reference_gaps() step 1.
            Expected keys: cited_title_hash, title, authors, year, count, intents, avg_quality.
        citing_paper_ids_by_gap: {cited_title_hash: [paper_id, ...]}
        citing_embeddings: {paper_id: embedding_vector} for citing papers
        section_centroids: {section: centroid_vector} from user's assigned papers
        sections_by_citing_paper: {paper_id: {section, ...}}

    Returns:
        Sorted list (score DESC) of enriched gap dicts with additional fields:
        score, intent_weight, semantic_factor, avg_quality, intents (list[str]),
        top_intent (str | None), sections_served (list[{section, count}]),
        cited_by_count (alias for count).
    """
    scored = []

    for gap in raw_gaps:
        cited_hash = gap.get("cited_title_hash", "")
        citing_ids = citing_paper_ids_by_gap.get(cited_hash, [])

        # Parse intents from GROUP_CONCAT string
        intents = _parse_intents(gap.get("intents"))
        intent_weight = _compute_intent_weight(intents)
        top_intent = _compute_top_intent(intents)

        # Semantic factor: penalty for irrelevant gaps (based on citing paper embeddings)
        semantic_factor = _compute_semantic_factor(
            citing_ids, citing_embeddings, section_centroids
        )

        # Quality from SQL COALESCE(quality_score, 3)
        avg_quality = float(gap.get("avg_quality") or 3.0)
        count = int(gap.get("count") or 0)

        score = count * avg_quality * intent_weight * semantic_factor

        # Sections served: aggregate from citing papers
        section_counts: dict[str, int] = {}
        for pid in citing_ids:
            for sec in sections_by_citing_paper.get(pid, set()):
                section_counts[sec] = section_counts.get(sec, 0) + 1
        sections_served = sorted(
            [{"section": s, "count": c} for s, c in section_counts.items()],
            key=lambda x: -x["count"],
        )

        enriched = dict(gap)
        enriched.update({
            "score": round(score, 3),
            "intent_weight": round(intent_weight, 3),
            "semantic_factor": round(semantic_factor, 3),
            "avg_quality": round(avg_quality, 3),
            "intents": sorted(set(intents)) if intents else [],
            "top_intent": top_intent,
            "sections_served": sections_served,
            "cited_by_count": count,  # backward-compat alias
        })
        scored.append(enriched)

    scored.sort(key=lambda g: -g["score"])
    return scored
