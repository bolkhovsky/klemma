"""LLM-curated library recommendations — scoring + context builders.

Pure-ish module for the ``GET /library/recommendations`` endpoint. Only
``select_loaded_sources`` reads from injected stores; everything else is pure.

Pipeline (matches ``.claude/plans/ok-hazy-dongarra.md``):

    candidates = compute_scored_gaps(..., limit=50)     # scored, без recency
    loaded     = select_loaded_sources(..., max_items=5)
    ctx        = build_prompt_inputs(...)
    llm_out    = ai.call_json(system=..., user=...)
    items      = parse_llm_output(llm_out) or fallback_recency_filter(candidates)[:10]

The full candidate-pool formula remains ``count × avg_quality × intent_weight
× semantic_factor`` (Teufel 2006 taxonomy); the LLM applies higher-level
semantic and topical judgement on top of that score.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from typing import Any, Optional

from .scoring import score_gaps

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CANDIDATE_LIMIT = 50
LOADED_SOURCES_LIMIT = 5
ABSTRACT_PREVIEW_CHARS = 1500
RATIONALE_LANGUAGE_DEFAULT = "Russian"
RECENCY_MAX_AGE_YEARS = 10
RECENCY_CLASSIC_MIN_CITED_BY = 3
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


# ---------------------------------------------------------------------------
# 1. Candidate pool — scored gaps without recency filter
# ---------------------------------------------------------------------------


def compute_scored_gaps(
    *,
    paper_store,
    library,
    project_store,
    user_id: str,
    limit: int = CANDIDATE_LIMIT,
) -> list[dict]:
    """Score reference gaps for a user without applying the recency filter.

    Mirrors the in-endpoint pipeline that ``list_reference_gaps`` runs, but
    stops before recency filtering so two different endpoints can consume the
    same scored pool with their own downstream policy.

    Returns an empty list if the user has no sources or no gaps are found.
    """
    user_sources = library.get_all_sources(user_id=user_id)
    if not user_sources:
        return []

    user_paper_ids = [s.paper_id for s in user_sources]

    raw_gaps, citing_by_hash = paper_store.get_reference_gaps(
        paper_ids=user_paper_ids,
        user_id=user_id,
        limit=max(limit * 4, 200),
    )
    if not raw_gaps:
        return []

    all_user_paper_id_to_citekey: dict[str, str] = library.get_citekey_map(
        user_paper_ids, user_id
    )
    all_citing_ids = list({pid for pids in citing_by_hash.values() for pid in pids})

    all_citing_citekeys = [
        all_user_paper_id_to_citekey[pid]
        for pid in all_citing_ids
        if pid in all_user_paper_id_to_citekey
    ]
    citekey_sections: dict[str, set[str]] = project_store.get_source_sections_bulk(
        all_citing_citekeys, user_id
    )
    sections_by_citing_paper: dict[str, set[str]] = {
        pid: citekey_sections.get(all_user_paper_id_to_citekey[pid], set())
        for pid in all_citing_ids
        if pid in all_user_paper_id_to_citekey
    }

    citing_embeddings: dict[str, list[float]] = paper_store.get_paper_embeddings_batch(
        all_citing_ids
    )

    section_centroids: dict[str, list[float]] = {}
    all_user_embeddings = paper_store.get_paper_embeddings_batch(user_paper_ids)
    if all_user_embeddings:
        section_centroids = project_store.get_section_centroids(
            user_id, all_user_embeddings, all_user_paper_id_to_citekey
        )

    scored = score_gaps(
        raw_gaps,
        citing_by_hash,
        citing_embeddings,
        section_centroids,
        sections_by_citing_paper,
    )
    return scored[:limit]


def apply_recency_filter(
    scored: list[dict],
    *,
    today_year: Optional[int] = None,
    max_age_years: int = RECENCY_MAX_AGE_YEARS,
    classic_min_cited_by: int = RECENCY_CLASSIC_MIN_CITED_BY,
) -> list[dict]:
    """Drop papers older than ``max_age_years`` unless classic (cited ≥N).

    Used by the ``/library/gaps`` endpoint and by the recommendations
    endpoint's fallback branch when the LLM call fails.
    """
    current_year = today_year if today_year is not None else date.today().year
    return [
        g for g in scored
        if not (
            g.get("year")
            and isinstance(g["year"], int)
            and (current_year - g["year"]) > max_age_years
            and g.get("cited_by_count", 0) < classic_min_cited_by
        )
    ]


# ---------------------------------------------------------------------------
# 2. Loaded sources selection
# ---------------------------------------------------------------------------


def select_loaded_sources(
    *,
    paper_store,
    library,
    user_id: str,
    max_items: int = LOADED_SOURCES_LIMIT,
) -> list[dict]:
    """Pick up to ``max_items`` completed sources for the LLM prompt context.

    Strategy:
    1. ``completed`` status only
    2. ``paper = paper_store.get_paper_by_id(src.paper_id)``; skip if missing
    3. ``raw_text = paper_store.get_raw_text(src.paper_id)`` (separate call;
       ``get_paper_by_id`` does not return raw_text)
    4. preview = abstract OR raw_text[:1500] OR ""
    5. sort by ``len(preview) DESC`` (filled papers first), tie-break by
       ``added_at DESC`` when the library exposes it
    6. return first ``max_items``
    """
    try:
        all_sources = library.get_all_sources(user_id=user_id)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("select_loaded_sources: library.get_all_sources failed: %s", exc)
        return []

    enriched: list[dict] = []
    for src in all_sources:
        if getattr(src, "status", None) != "completed":
            continue
        paper = paper_store.get_paper_by_id(src.paper_id)
        if paper is None:
            continue

        abstract = (paper.abstract or "").strip() if paper.abstract else ""
        raw_text = ""
        if not abstract:
            try:
                raw_text = (paper_store.get_raw_text(src.paper_id) or "").strip()
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug("get_raw_text failed for %s: %s", src.paper_id, exc)
                raw_text = ""

        if abstract:
            preview = abstract[:ABSTRACT_PREVIEW_CHARS]
        elif raw_text:
            preview = raw_text[:ABSTRACT_PREVIEW_CHARS]
        else:
            preview = ""

        enriched.append({
            "citekey": getattr(src, "citekey", "") or "",
            "paper_id": src.paper_id,
            "title": paper.title or "",
            "authors": paper.authors or "",
            "year": paper.year,
            "doi": paper.doi,
            "preview": preview,
            "_preview_len": len(preview),
            "_added_at": getattr(src, "added_at", "") or "",
        })

    enriched.sort(key=lambda s: (-s["_preview_len"], s["_added_at"]), reverse=False)
    # reverse=False + negative len gives DESC on len; added_at ASC as tie-break
    # (prefer papers added earlier — they've been in the library longer and
    # are more likely representative of the user's stable topic). Intentional.

    # Drop internal sort fields from returned payload
    cleaned = []
    for s in enriched[:max_items]:
        cleaned.append({k: v for k, v in s.items() if not k.startswith("_")})
    return cleaned


# ---------------------------------------------------------------------------
# 3. Hashing
# ---------------------------------------------------------------------------


def compute_library_state_hash(user_sources) -> str:
    """Stable SHA256 over sorted (paper_id, status) pairs."""
    items = sorted(
        (getattr(s, "paper_id", "") or "", getattr(s, "status", "") or "")
        for s in user_sources
    )
    payload = json.dumps(items, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_outline_hash(outline: Optional[list]) -> str:
    """Stable SHA256 over outline.

    Order-insensitive: perturbing section order does NOT change the hash,
    renaming or add/remove does. ``outline`` may be None, [] or list of
    ``{id, name}`` dicts (or Pydantic models).
    """
    if not outline:
        payload = json.dumps({}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    sections: dict[str, str] = {}
    for s in outline:
        if hasattr(s, "id") and hasattr(s, "name"):
            sid = str(getattr(s, "id", "") or "")
            name = str(getattr(s, "name", "") or "")
        elif isinstance(s, dict):
            sid = str(s.get("id", "") or "")
            name = str(s.get("name", "") or "")
        else:
            continue
        sections[sid] = name

    payload = json.dumps(sections, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 4. Rationale language heuristic
# ---------------------------------------------------------------------------


def detect_rationale_language(project_name: Optional[str]) -> str:
    """Return ``"Russian"`` or ``"English"`` based on project name characters.

    Fallback on empty/None → Russian (matches current production audience).
    """
    if not project_name:
        return RATIONALE_LANGUAGE_DEFAULT
    if _CYRILLIC_RE.search(project_name):
        return "Russian"
    return "English"


# ---------------------------------------------------------------------------
# 5. Prompt context builders
# ---------------------------------------------------------------------------


def _format_loaded_sources_md(loaded_sources: list[dict]) -> str:
    """Markdown list of loaded sources for the LLM prompt."""
    if not loaded_sources:
        return "_(нет обработанных источников)_"
    lines = []
    for src in loaded_sources:
        title = src.get("title") or "(без названия)"
        authors = src.get("authors") or ""
        year = src.get("year") or ""
        preview = (src.get("preview") or "").strip()
        head = f"- **{title}**"
        if authors or year:
            head += f" — {authors}{', ' + str(year) if year else ''}"
        if preview:
            head += f"\n  {preview}"
        lines.append(head)
    return "\n".join(lines)


def _format_outline_md(outline: Optional[list]) -> str:
    """Markdown list of outline sections."""
    if not outline:
        return "_(outline не задан)_"
    lines = []
    for s in outline:
        if hasattr(s, "id") and hasattr(s, "name"):
            sid = str(getattr(s, "id", "") or "")
            name = str(getattr(s, "name", "") or "")
        elif isinstance(s, dict):
            sid = str(s.get("id", "") or "")
            name = str(s.get("name", "") or "")
        else:
            continue
        lines.append(f"- {sid}. {name}" if sid else f"- {name}")
    return "\n".join(lines) if lines else "_(outline не задан)_"


def _format_candidates_md(candidates: list[dict]) -> str:
    """Markdown list of candidate gaps with score/intent hints."""
    if not candidates:
        return "_(кандидатов нет)_"
    lines = []
    for i, c in enumerate(candidates, start=1):
        title = c.get("title") or "(без названия)"
        authors = c.get("authors") or ""
        year = c.get("year") or ""
        count = c.get("cited_by_count", 0)
        top_intent = c.get("top_intent") or "—"
        doi = c.get("doi") or ""
        meta = f"cited_by={count}, intent={top_intent}"
        if doi:
            meta += f", doi={doi}"
        lines.append(
            f"{i}. **{title}** — {authors}{', ' + str(year) if year else ''} "
            f"({meta})"
        )
    return "\n".join(lines)


def build_prompt_inputs(
    *,
    project_name: str,
    outline: Optional[list],
    loaded_sources: list[dict],
    candidates: list[dict],
    rationale_language: str,
) -> dict:
    """Assemble the Jinja context passed to both prompt templates."""
    return {
        "project_name": project_name or "",
        "outline_md": _format_outline_md(outline),
        "loaded_sources_md": _format_loaded_sources_md(loaded_sources),
        "candidates_md": _format_candidates_md(candidates),
        "rationale_language": rationale_language,
        "max_recommendations": 10,
    }


# ---------------------------------------------------------------------------
# 6. Parsing LLM output
# ---------------------------------------------------------------------------


def _clamp_score(raw: Any) -> float:
    """Clamp score into [1.0, 10.0]; non-numeric → 5.0."""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 5.0
    if val < 1.0:
        return 1.0
    if val > 10.0:
        return 10.0
    return val


def invalidate_for_user(paper_store, user_id: str, project_id: Optional[str] = None) -> None:
    """Fire-and-forget cache invalidation on library or outline mutation.

    Wraps ``LocalPaperStore.invalidate_recommendations_cache`` but never raises
    — keeps the calling mutation path's happy-path unaffected by cache issues.
    ``project_id=None`` drops all cached rows for the user (used on library
    mutations where any project's cache could be stale).
    """
    try:
        paper_store.invalidate_recommendations_cache(user_id, project_id)
    except Exception as exc:  # pragma: no cover — non-fatal
        logger.debug(
            "Recommendation cache invalidation failed for user=%s project=%s: %s",
            user_id, project_id, exc,
        )


def parse_llm_output(raw: Any) -> list[dict]:
    """Tolerant parser: accepts dict from ``call_json`` or a JSON string.

    Drops items without ``title``. Clamps ``score`` into [1,10]. Never raises.
    """
    if raw is None:
        return []

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []

    if not isinstance(raw, dict):
        return []

    items = raw.get("recommendations")
    if not isinstance(items, list):
        return []

    cleaned: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        if not title:
            continue
        authors = item.get("authors") or ""
        year = item.get("year")
        if year is not None and not isinstance(year, int):
            try:
                year = int(year)
            except (TypeError, ValueError):
                year = None
        cleaned.append({
            "title": title,
            "authors": authors,
            "year": year,
            "doi": item.get("doi") or None,
            "rationale": (item.get("rationale") or "").strip(),
            "score": _clamp_score(item.get("score", 5.0)),
        })
    return cleaned
