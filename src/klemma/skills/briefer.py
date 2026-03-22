"""Guided Serendipity briefing skill — analyzes new sources and generates branching points."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..ai import AIProvider
from ..config import KlemmaConfig, resolve_prompt
from ..embeddings import EmbeddingProvider, cosine_similarity

logger = logging.getLogger(__name__)


@dataclass
class SimilarSource:
    """A source similar to the new one, found via embedding search."""

    citekey: str
    title: str
    year: Optional[int] = None
    similarity: float = 0.0
    fragments: list[str] = field(default_factory=list)


@dataclass
class BriefingResult:
    """Result of a briefing analysis for a new source."""

    source_citekey: str
    key_claims: list[str] = field(default_factory=list)
    connections: list[dict] = field(default_factory=list)
    niches: list[str] = field(default_factory=list)
    forks: list[dict] = field(default_factory=list)
    recommended_sections: list[str] = field(default_factory=list)
    similar_sources: list[SimilarSource] = field(default_factory=list)
    error: Optional[str] = None


def find_similar_sources(
    source_citekey: str,
    state,
    embeddings: Optional[EmbeddingProvider] = None,
    top_k: int = 7,
) -> list[SimilarSource]:
    """Find sources most similar to the given one via embedding cosine similarity."""
    if not embeddings:
        return []

    target_embedding = state.get_embedding(source_citekey)
    if not target_embedding:
        return []

    all_embeddings = state.get_all_embeddings()
    if not all_embeddings:
        return []

    scored = []
    for citekey, vec in all_embeddings.items():
        if citekey == source_citekey:
            continue
        sim = cosine_similarity(target_embedding, vec)
        if sim > 0.3:
            scored.append((citekey, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    results = []
    for citekey, sim in scored[:top_k]:
        source = state.get_source(citekey)
        title = source.get("title", "") if source else ""
        year = source.get("year") if source else None

        # Get a few fragment texts for context
        frags = state.get_fragments(citekey) if hasattr(state, "get_fragments") else []
        frag_texts = [
            f.get("fragment_text", "")[:200]
            for f in (frags[:3] if isinstance(frags, list) else [])
        ]

        results.append(SimilarSource(
            citekey=citekey,
            title=title,
            year=year,
            similarity=sim,
            fragments=frag_texts,
        ))

    return results


def generate_briefing(
    source_citekey: str,
    config: KlemmaConfig,
    state,
    ai: AIProvider,
    dissertation_context: str = "",
    embeddings: Optional[EmbeddingProvider] = None,
    klemma_home: Optional[Path] = None,
    project_root: Optional[Path] = None,
    language: str = "Russian",
) -> BriefingResult:
    """Generate a Guided Serendipity briefing for a newly acquired source.

    The briefing extracts key claims, finds connections to the existing library,
    identifies niches, and proposes 2-3 branching directions (forks).
    """
    source = state.get_source(source_citekey)
    if not source:
        return BriefingResult(
            source_citekey=source_citekey,
            error=f"Source @{source_citekey} not found in database",
        )

    # Gather source metadata
    title = source.get("title", source_citekey)
    authors = source.get("authors", "")
    year = source.get("year")
    abstract = source.get("abstract", "")

    # Get fragments
    fragments_raw = state.fragments.get_fragments(source_citekey)
    fragments = [dict(f) for f in fragments_raw] if fragments_raw else []

    if not fragments and not abstract:
        return BriefingResult(
            source_citekey=source_citekey,
            error=f"No fragments or abstract for @{source_citekey}. Run 'klemma process' first.",
        )

    # Find similar sources
    similar = find_similar_sources(source_citekey, state, embeddings, top_k=7)

    # Load previous decisions for context
    previous_decisions = []
    if hasattr(state, "decisions"):
        previous_decisions = state.decisions.get_decisions_for_context()

    # Load outline summary
    outline_summary = ""
    if project_root:
        from .context_loader import load_outline_context
        outline_ctx = load_outline_context(None, project_root)
        if outline_ctx.get("description"):
            outline_summary = outline_ctx["description"]
        if outline_ctx.get("current_chapter_desc"):
            outline_summary += "\n" + outline_ctx["current_chapter_desc"]

    # Render prompt
    prompt_path = (
        resolve_prompt("briefing.md", klemma_home)
        if klemma_home
        else Path(__file__).parent.parent.parent.parent / "prompts" / "briefing.md"
    )
    prompt = ai.render_prompt(
        prompt_path,
        language=language,
        dissertation_context=dissertation_context,
        outline_summary=outline_summary,
        source_citekey=source_citekey,
        source_title=title,
        source_authors=authors,
        source_year=year,
        abstract=abstract,
        fragments=fragments,
        similar_sources=similar,
        previous_decisions=previous_decisions,
    )

    # Call LLM
    result = ai.call_json(prompt)
    if not result:
        return BriefingResult(
            source_citekey=source_citekey,
            similar_sources=similar,
            error="LLM call returned no result",
        )

    return BriefingResult(
        source_citekey=source_citekey,
        key_claims=result.get("key_claims", []),
        connections=result.get("connections", []),
        niches=result.get("niches", []),
        forks=result.get("forks", []),
        recommended_sections=result.get("recommended_sections", []),
        similar_sources=similar,
    )


def save_briefing_as_decision(
    briefing: BriefingResult,
    state,
) -> Optional[int]:
    """Save a briefing result as a pending decision in the Branch Store.

    Returns the decision ID, or None if briefing has no forks.
    """
    if not briefing.forks or briefing.error:
        return None

    context = {
        "key_claims": briefing.key_claims,
        "connections": briefing.connections,
        "niches": briefing.niches,
        "similar_sources": [
            {"citekey": s.citekey, "title": s.title, "similarity": s.similarity}
            for s in briefing.similar_sources[:5]
        ],
    }

    options = [
        {
            "key": fork.get("key", chr(65 + i)),
            "title": fork.get("title", f"Option {chr(65 + i)}"),
            "description": fork.get("description", ""),
        }
        for i, fork in enumerate(briefing.forks)
    ]

    sections = briefing.recommended_sections or []
    # Also collect sections from forks
    for fork in briefing.forks:
        for s in fork.get("sections", []):
            if s not in sections:
                sections.append(s)

    # Find previous decisions to link influenced_by
    influenced_by = None
    if hasattr(state, "decisions"):
        trail = state.decisions.get_trail()
        if trail:
            influenced_by = [trail[-1]["id"]]

    return state.decisions.save_decision(
        trigger_type="briefing",
        trigger_source=briefing.source_citekey,
        context=context,
        options=options,
        sections=sections if sections else None,
        influenced_by=influenced_by,
    )
