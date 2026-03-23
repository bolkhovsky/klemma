"""Guided Serendipity insights — detect blind spots and hidden clusters in the library."""

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..ai import AIProvider
from ..config import KlemmaConfig, resolve_prompt
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


# ── Stage 2-3: Curated insights pipeline ─────────────────────────────────


@dataclass
class RawCandidate:
    """A raw insight candidate from Stage 1 (blind spot or hidden cluster)."""

    candidate_type: str  # "blind_spot" | "hidden_cluster"
    section: str  # primary section
    sections: list[str] = field(default_factory=list)
    severity: str = ""  # high | medium (blind spots only)
    source_count: int = 0
    average_count: float = 0.0
    gap_count: int = 0
    # Hidden cluster fields
    citekey_a: str = ""
    citekey_b: str = ""
    similarity: float = 0.0
    section_a: str = ""
    section_b: str = ""
    title_a: str = ""
    title_b: str = ""


@dataclass
class CuratedInsight:
    """An LLM-curated insight with trajectory and actionable options."""

    title: str
    explanation: str  # WHY this matters
    trajectory: str  # WHERE this leads
    diversity_tag: str  # methodology | bridge | gap | anomaly
    novelty_score: float = 0.0  # 0-1
    actionability_score: float = 0.0  # 0-1
    candidate_type: str = ""  # blind_spot | hidden_cluster
    sections: list[str] = field(default_factory=list)
    options: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)


@dataclass
class CuratedInsightsResult:
    """Result of the curated insights pipeline."""

    insights: list[CuratedInsight] = field(default_factory=list)
    decision_ids: list[int] = field(default_factory=list)
    raw_count: int = 0
    suppressed_count: int = 0
    curated_count: int = 0
    blocked: bool = False
    pending_count: int = 0


def _candidates_from_insights(result: InsightsResult) -> list[RawCandidate]:
    """Convert InsightsResult into a flat list of RawCandidates."""
    candidates = []
    for spot in result.blind_spots:
        if spot.severity not in ("medium", "high"):
            continue
        candidates.append(RawCandidate(
            candidate_type="blind_spot",
            section=spot.section,
            sections=[spot.section],
            severity=spot.severity,
            source_count=spot.source_count,
            average_count=spot.average_count,
            gap_count=spot.gap_count,
        ))
    for cluster in result.hidden_clusters:
        candidates.append(RawCandidate(
            candidate_type="hidden_cluster",
            section=cluster.section_a,
            sections=[cluster.section_a, cluster.section_b],
            similarity=cluster.similarity,
            citekey_a=cluster.citekey_a,
            citekey_b=cluster.citekey_b,
            section_a=cluster.section_a,
            section_b=cluster.section_b,
            title_a=cluster.title_a,
            title_b=cluster.title_b,
        ))
    return candidates


def suppress_candidates(
    candidates: list[RawCandidate],
    state,
) -> list[RawCandidate]:
    """Stage 2: Heuristic suppression — no LLM calls.

    Removes:
    - Already-decided sections (insight decisions already resolved for that section)
    - Trivial clusters (similarity < 0.80)
    - Duplicate section pairs (keep highest similarity)
    - Same-chapter redundant blind spots (keep worst per chapter)

    Based on Phansalkar et al. 2013 — suppression-first reduces ~36%.
    """
    # Get sections that already have actively decided insight decisions
    # (skipped decisions do NOT suppress — only real choices A/B/C)
    decided_sections: set[str] = set()
    try:
        decided = state.decisions.get_decisions(
            trigger_type="insight", include_skipped=False
        )
        for d in decided:
            if d.get("chosen_option") is not None:
                secs = d.get("sections", [])
                if isinstance(secs, list):
                    decided_sections.update(secs)
    except Exception:
        pass

    survivors = []
    seen_section_pairs: set[tuple[str, str]] = set()
    seen_chapter_blind_spots: dict[str, RawCandidate] = {}

    for c in candidates:
        # Rule 1: Skip already-decided sections
        if all(s in decided_sections for s in c.sections):
            logger.debug("Suppressed: all sections already decided: %s", c.sections)
            continue

        # Rule 2: Trivial clusters (< 0.80 similarity)
        if c.candidate_type == "hidden_cluster" and c.similarity < 0.80:
            logger.debug(
                "Suppressed: trivial cluster %.3f < 0.80: %s ↔ %s",
                c.similarity, c.citekey_a, c.citekey_b,
            )
            continue

        # Rule 3: Duplicate section pairs (keep highest similarity)
        if c.candidate_type == "hidden_cluster":
            pair = tuple(sorted([c.section_a, c.section_b]))
            if pair in seen_section_pairs:
                logger.debug("Suppressed: duplicate section pair %s", pair)
                continue
            seen_section_pairs.add(pair)

        # Rule 4: Same-chapter redundant blind spots (keep worst per chapter)
        if c.candidate_type == "blind_spot":
            chapter = c.section.split(".")[0] if "." in c.section else c.section
            existing = seen_chapter_blind_spots.get(chapter)
            if existing is not None:
                # Keep the one with fewer sources (worse)
                if c.source_count < existing.source_count:
                    # Replace — remove existing from survivors, add new
                    survivors = [s for s in survivors if s is not existing]
                    seen_chapter_blind_spots[chapter] = c
                else:
                    logger.debug(
                        "Suppressed: redundant blind spot %s (chapter %s)",
                        c.section, chapter,
                    )
                    continue
            else:
                seen_chapter_blind_spots[chapter] = c

        survivors.append(c)

    logger.info(
        "Suppression: %d → %d candidates (%.0f%% reduction)",
        len(candidates), len(survivors),
        (1 - len(survivors) / len(candidates)) * 100 if candidates else 0,
    )
    return survivors


def check_insights_blocked(state) -> tuple[bool, int, list[dict]]:
    """Check if pending insight decisions exist (blocking new generation).

    Returns (is_blocked, pending_count, pending_decisions).
    """
    pending = state.decisions.get_pending_decisions(trigger_type="insight")
    return len(pending) > 0, len(pending), pending


def curate_insights(
    candidates: list[RawCandidate],
    *,
    config: KlemmaConfig,
    ai: AIProvider,
    state,
    dissertation_context: str = "",
    klemma_home=None,
    project_root=None,
    project_chain=None,
    language: str = "Russian",
) -> list[CuratedInsight]:
    """Stage 3: LLM curation — one call, multi-objective ranking.

    Based on:
    - Nadkarni et al. 2025: LLM-as-a-judge for LBD filtering
    - McNee et al. 2006: multi-objective (novelty + diversity + actionability)
    - Si et al. 2024: enforce diversity via structural constraint
    - Hummon & Doreian 1989: trajectory in every insight
    - Kastrin et al. 2025: every insight must explain WHY and project WHERE
    """
    if not candidates:
        return []

    # Gather feedback history for prompt injection
    feedback_summary = state.decisions.get_feedback_summary()

    # Build candidates text for prompt
    candidates_text = _format_candidates_for_prompt(candidates)

    # Resolve prompt template
    prompt_path = resolve_prompt(
        "insight_curator.md",
        klemma_home or "",
        project_chain or [],
    )
    if not prompt_path:
        logger.error("insight_curator.md prompt not found")
        return []

    prompt_text = ai.render_prompt(
        prompt_path,
        dissertation_context=dissertation_context,
        candidates=candidates_text,
        candidate_count=len(candidates),
        feedback_summary=_format_feedback_for_prompt(feedback_summary),
        language=language,
    )

    response = ai.call_json(
        prompt_text,
        "Select and rank the top 3-5 insights. Respond with JSON only.",
        max_tokens=4096,
    )
    if not response:
        logger.error("LLM curation returned empty response")
        return []

    return _parse_curated_insights(response, candidates)


def _format_candidates_for_prompt(candidates: list[RawCandidate]) -> str:
    """Format raw candidates as text for the LLM prompt."""
    lines = []
    for i, c in enumerate(candidates, 1):
        if c.candidate_type == "blind_spot":
            lines.append(
                f"{i}. BLIND SPOT: section {c.section} has {c.source_count} sources "
                f"(average: {c.average_count}), {c.gap_count} reference gaps, "
                f"severity: {c.severity}"
            )
        elif c.candidate_type == "hidden_cluster":
            lines.append(
                f"{i}. HIDDEN CLUSTER: @{c.citekey_a} ({c.section_a}) ↔ "
                f"@{c.citekey_b} ({c.section_b}), similarity: {c.similarity:.3f}"
            )
            if c.title_a:
                lines.append(f"   A: {c.title_a[:80]}")
            if c.title_b:
                lines.append(f"   B: {c.title_b[:80]}")
    return "\n".join(lines)


def _format_feedback_for_prompt(feedback: dict) -> str:
    """Format feedback summary for prompt injection."""
    parts = []
    if feedback.get("total_liked") or feedback.get("total_disliked"):
        liked = feedback.get("liked_types", {})
        disliked = feedback.get("disliked_types", {})
        if liked:
            items = ", ".join(f"{v} {k}" for k, v in liked.items())
            parts.append(f"- Liked: {items}")
        if disliked:
            items = ", ".join(f"{v} {k}" for k, v in disliked.items())
            parts.append(f"- Disliked: {items}")

    notes = feedback.get("recent_notes", [])
    if notes:
        for note in notes[:5]:
            parts.append(f"- Recent note: \"{note}\"")

    return "\n".join(parts) if parts else "No feedback yet."


def _parse_curated_insights(
    response: dict,
    candidates: list[RawCandidate],
) -> list[CuratedInsight]:
    """Parse LLM JSON response into CuratedInsight objects."""
    raw_insights = response.get("insights", [])
    if not isinstance(raw_insights, list):
        logger.error("LLM response 'insights' is not a list")
        return []

    # Build candidate lookup by index (1-based in prompt)
    candidate_map = {i + 1: c for i, c in enumerate(candidates)}

    curated = []
    seen_tags: dict[str, int] = {}

    for item in raw_insights[:5]:  # Max 5 per Paterno 2009
        if not isinstance(item, dict):
            continue

        tag = item.get("diversity_tag", "unknown")

        # Enforce max 2 per diversity_tag (Si 2024)
        if seen_tags.get(tag, 0) >= 2:
            logger.debug("Diversity cap: skipping extra '%s' insight", tag)
            continue
        seen_tags[tag] = seen_tags.get(tag, 0) + 1

        # Resolve source candidate
        candidate_idx = item.get("candidate_index", 0)
        source_candidate = candidate_map.get(candidate_idx)

        # Build options (3-tier: Act / Bookmark / Dismiss per Paterno 2009)
        sections = item.get("sections", [])
        if source_candidate:
            sections = sections or source_candidate.sections

        options = [
            {
                "key": "A",
                "title": item.get("action_title", "Act on this"),
                "description": item.get("action_description", "Investigate this insight"),
            },
            {
                "key": "B",
                "title": "Bookmark",
                "description": "Save for later — interesting but not urgent",
            },
            {
                "key": "C",
                "title": "Dismiss",
                "description": "Not relevant — suppress similar insights",
            },
        ]

        context = {}
        if source_candidate:
            context["candidate_type"] = source_candidate.candidate_type
            if source_candidate.candidate_type == "blind_spot":
                context["type"] = "blind_spot"
                context["section"] = source_candidate.section
                context["source_count"] = source_candidate.source_count
                context["average_count"] = source_candidate.average_count
                context["gap_count"] = source_candidate.gap_count
            elif source_candidate.candidate_type == "hidden_cluster":
                context["type"] = "hidden_cluster"
                context["citekey_a"] = source_candidate.citekey_a
                context["citekey_b"] = source_candidate.citekey_b
                context["similarity"] = source_candidate.similarity
                context["section_a"] = source_candidate.section_a
                context["section_b"] = source_candidate.section_b
                context["title_a"] = source_candidate.title_a
                context["title_b"] = source_candidate.title_b

        curated.append(CuratedInsight(
            title=item.get("title", "Untitled insight"),
            explanation=item.get("explanation", ""),
            trajectory=item.get("trajectory", ""),
            diversity_tag=tag,
            novelty_score=float(item.get("novelty_score", 0)),
            actionability_score=float(item.get("actionability_score", 0)),
            candidate_type=source_candidate.candidate_type if source_candidate else "",
            sections=sections,
            options=options,
            context=context,
        ))

    return curated


def save_curated_insights_as_decisions(
    insights: list[CuratedInsight], state
) -> list[int]:
    """Save curated insights as pending decisions with 3-tier options."""
    decision_ids = []
    for insight in insights:
        context = {
            **insight.context,
            "title": insight.title,
            "explanation": insight.explanation,
            "trajectory": insight.trajectory,
            "diversity_tag": insight.diversity_tag,
        }
        did = state.decisions.save_decision(
            trigger_type="insight",
            context=context,
            options=insight.options,
            sections=insight.sections,
        )
        decision_ids.append(did)
    return decision_ids


def generate_curated_insights(
    state,
    *,
    config: KlemmaConfig,
    ai: Optional[AIProvider] = None,
    project_store=None,
    dissertation_context: str = "",
    klemma_home=None,
    project_root=None,
    project_chain=None,
    language: str = "Russian",
    raw_mode: bool = False,
) -> CuratedInsightsResult:
    """Full pipeline: generate → suppress → curate (or raw mode).

    Args:
        raw_mode: If True, skip curation and return all raw candidates (old behavior).
    """
    # Check blocking first
    is_blocked, pending_count, pending = check_insights_blocked(state)
    if is_blocked:
        return CuratedInsightsResult(
            blocked=True,
            pending_count=pending_count,
        )

    # Stage 1: Generate broadly
    raw_result = generate_insights(state, project_store)
    all_candidates = _candidates_from_insights(raw_result)

    if not all_candidates:
        return CuratedInsightsResult(raw_count=0)

    # Raw mode — save all as decisions (old behavior)
    if raw_mode:
        decision_ids = save_insights_as_decisions(raw_result, state)
        return CuratedInsightsResult(
            raw_count=len(all_candidates),
            suppressed_count=0,
            curated_count=len(decision_ids),
        )

    # Stage 2: Heuristic suppression
    survivors = suppress_candidates(all_candidates, state)

    if not survivors:
        return CuratedInsightsResult(
            raw_count=len(all_candidates),
            suppressed_count=len(all_candidates),
        )

    # Stage 3: LLM curation (requires AI)
    if not ai:
        # Fallback: save suppressed candidates as decisions without curation
        # Convert survivors back to InsightsResult for save_insights_as_decisions
        logger.warning("No AI backend — saving suppressed candidates without curation")
        decision_ids = _save_raw_candidates_as_decisions(survivors, state)
        return CuratedInsightsResult(
            raw_count=len(all_candidates),
            suppressed_count=len(all_candidates) - len(survivors),
            curated_count=len(decision_ids),
        )

    curated = curate_insights(
        survivors,
        config=config,
        ai=ai,
        state=state,
        dissertation_context=dissertation_context,
        klemma_home=klemma_home,
        project_root=project_root,
        project_chain=project_chain,
        language=language,
    )

    # Save curated insights as decisions
    decision_ids = save_curated_insights_as_decisions(curated, state)

    return CuratedInsightsResult(
        insights=curated,
        decision_ids=decision_ids,
        raw_count=len(all_candidates),
        suppressed_count=len(all_candidates) - len(survivors),
        curated_count=len(curated),
    )


def _save_raw_candidates_as_decisions(
    candidates: list[RawCandidate], state
) -> list[int]:
    """Save raw candidates as decisions (fallback when no AI)."""
    decision_ids = []
    for c in candidates:
        if c.candidate_type == "blind_spot":
            context = {
                "type": "blind_spot",
                "section": c.section,
                "source_count": c.source_count,
                "average_count": c.average_count,
                "gap_count": c.gap_count,
            }
            options = [
                {"key": "A", "title": "Search for sources",
                 "description": f"Use 'klemma suggest' for section {c.section}"},
                {"key": "B", "title": "Intentional gap",
                 "description": "Skip — fewer sources is intentional"},
            ]
        else:
            context = {
                "type": "hidden_cluster",
                "citekey_a": c.citekey_a,
                "citekey_b": c.citekey_b,
                "similarity": c.similarity,
                "section_a": c.section_a,
                "section_b": c.section_b,
                "title_a": c.title_a,
                "title_b": c.title_b,
            }
            options = [
                {"key": "A", "title": "Explore connection",
                 "description": f"@{c.citekey_a} ↔ @{c.citekey_b} — investigate"},
                {"key": "B", "title": "Not relevant",
                 "description": "Similarity is coincidental"},
            ]
        did = state.decisions.save_decision(
            trigger_type="insight",
            context=context,
            options=options,
            sections=c.sections,
        )
        decision_ids.append(did)
    return decision_ids
