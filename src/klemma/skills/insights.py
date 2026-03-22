"""Guided Serendipity insights — detect blind spots and hidden clusters in the library."""

import logging
from dataclasses import dataclass, field

from ..embeddings import cosine_similarity

logger = logging.getLogger(__name__)


@dataclass
class BlindSpot:
    """A section with significantly fewer sources than average."""

    section: str
    source_count: int
    average_count: float
    gap_count: int = 0
    severity: str = "low"  # low | medium | high


@dataclass
class HiddenCluster:
    """A pair of sources from different sections that are semantically close."""

    citekey_a: str
    citekey_b: str
    similarity: float
    section_a: str
    section_b: str
    title_a: str = ""
    title_b: str = ""


@dataclass
class InsightsResult:
    """Combined insights from library analysis."""

    blind_spots: list[BlindSpot] = field(default_factory=list)
    hidden_clusters: list[HiddenCluster] = field(default_factory=list)
    total_sources: int = 0
    total_sections: int = 0


def detect_blind_spots(state, project_store=None) -> list[BlindSpot]:
    """Find sections with significantly fewer sources than average.

    Pure SQL — no LLM calls.
    """
    # Get coverage stats
    ps = project_store
    cov = (
        ps.get_coverage_stats()
        if ps and ps.count_sources() > 0
        else state.get_coverage_stats()
    )

    by_section = cov.get("sections", cov.get("by_section", {}))
    if not by_section:
        return []

    counts = list(by_section.values())
    if not counts:
        return []

    avg = sum(counts) / len(counts)
    if avg < 2:
        return []

    # Get ref-gap counts per section
    gap_summary = {}
    try:
        gaps = state.gaps.get_reference_gaps()
        for g in gaps:
            sections_raw = g.get("dissertation_sections", "")
            if sections_raw:
                for sec in str(sections_raw).split(","):
                    sec = sec.strip().strip('"').strip("'")
                    if sec:
                        gap_summary[sec] = gap_summary.get(sec, 0) + 1
    except Exception:
        pass

    spots = []
    for section, count in by_section.items():
        ratio = count / avg if avg > 0 else 0
        if ratio < 0.5:
            severity = "high" if ratio < 0.25 else "medium"
            gaps_in_section = gap_summary.get(section, 0)
            spots.append(BlindSpot(
                section=section,
                source_count=count,
                average_count=round(avg, 1),
                gap_count=gaps_in_section,
                severity=severity,
            ))

    spots.sort(key=lambda s: s.source_count)
    return spots


def detect_hidden_clusters(
    state,
    similarity_threshold: float = 0.75,
    max_pairs: int = 10,
) -> list[HiddenCluster]:
    """Find semantically similar sources assigned to different sections.

    Uses source embeddings — no LLM calls.
    """
    all_embeddings = state.get_all_embeddings()
    if not all_embeddings or len(all_embeddings) < 2:
        return []

    # Build section map: citekey → primary section
    section_map = {}
    for citekey in all_embeddings:
        source = state.get_source(citekey)
        if source:
            section_map[citekey] = source.get("primary_section", "")

    # Pairwise comparison (O(n²) but only on sources with embeddings)
    citekeys = list(all_embeddings.keys())
    pairs = []

    for i in range(len(citekeys)):
        for j in range(i + 1, len(citekeys)):
            ck_a, ck_b = citekeys[i], citekeys[j]
            sec_a = section_map.get(ck_a, "")
            sec_b = section_map.get(ck_b, "")

            # Only interested in cross-section pairs
            if not sec_a or not sec_b or sec_a == sec_b:
                continue

            sim = cosine_similarity(all_embeddings[ck_a], all_embeddings[ck_b])
            if sim >= similarity_threshold:
                src_a = state.get_source(ck_a)
                src_b = state.get_source(ck_b)
                pairs.append(HiddenCluster(
                    citekey_a=ck_a,
                    citekey_b=ck_b,
                    similarity=round(sim, 3),
                    section_a=sec_a,
                    section_b=sec_b,
                    title_a=src_a.get("title", "") if src_a else "",
                    title_b=src_b.get("title", "") if src_b else "",
                ))

    pairs.sort(key=lambda p: p.similarity, reverse=True)
    return pairs[:max_pairs]


def generate_insights(state, project_store=None) -> InsightsResult:
    """Run all insight detectors and return combined results."""
    blind_spots = detect_blind_spots(state, project_store)
    hidden_clusters = detect_hidden_clusters(state)

    stats = state.get_stats()
    cov = state.get_coverage_stats()
    sections = cov.get("sections", cov.get("by_section", {}))

    return InsightsResult(
        blind_spots=blind_spots,
        hidden_clusters=hidden_clusters,
        total_sources=stats.get("completed", 0) + stats.get("pending", 0),
        total_sections=len(sections),
    )


def save_insights_as_decisions(insights: InsightsResult, state) -> list[int]:
    """Save notable insights as pending decisions in the Branch Store.

    Returns list of created decision IDs.
    """
    decision_ids = []

    # Blind spots → decisions
    for spot in insights.blind_spots:
        if spot.severity not in ("medium", "high"):
            continue

        context = {
            "type": "blind_spot",
            "section": spot.section,
            "source_count": spot.source_count,
            "average_count": spot.average_count,
            "gap_count": spot.gap_count,
        }
        options = [
            {
                "key": "A",
                "title": "Search for sources",
                "description": f"Use 'klemma suggest' to find papers for section {spot.section}",
            },
            {
                "key": "B",
                "title": "Intentional gap",
                "description": "This section intentionally has fewer sources — skip",
            },
        ]

        did = state.decisions.save_decision(
            trigger_type="insight",
            context=context,
            options=options,
            sections=[spot.section],
        )
        decision_ids.append(did)

    # Hidden clusters → decisions
    for cluster in insights.hidden_clusters[:5]:
        context = {
            "type": "hidden_cluster",
            "citekey_a": cluster.citekey_a,
            "citekey_b": cluster.citekey_b,
            "similarity": cluster.similarity,
            "section_a": cluster.section_a,
            "section_b": cluster.section_b,
            "title_a": cluster.title_a,
            "title_b": cluster.title_b,
        }
        options = [
            {
                "key": "A",
                "title": "Explore connection",
                "description": (
                    f"@{cluster.citekey_a} ({cluster.section_a}) and "
                    f"@{cluster.citekey_b} ({cluster.section_b}) may have an "
                    f"undiscovered link — investigate"
                ),
            },
            {
                "key": "B",
                "title": "Not relevant",
                "description": "The similarity is coincidental — skip",
            },
        ]

        did = state.decisions.save_decision(
            trigger_type="insight",
            context=context,
            options=options,
            sections=[cluster.section_a, cluster.section_b],
        )
        decision_ids.append(did)

    return decision_ids
