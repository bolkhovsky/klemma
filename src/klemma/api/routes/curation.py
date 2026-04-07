"""Fragment curation endpoints — accept/reject fragments, assign to sections, suggest (ADR-009)."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from klemma.models import UserRecord
from klemma.section_types import SectionType, infer_section_type

from ..auth.deps import get_current_user, get_user_store
from ..deps import get_paper_store, get_user_library

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Intent → section type mapping
# ---------------------------------------------------------------------------

INTENT_TO_SECTION_TYPES: dict[str, list[SectionType]] = {
    "background": [SectionType.INTRODUCTION, SectionType.BACKGROUND, SectionType.LITERATURE_REVIEW],
    "method": [SectionType.METHODOLOGY, SectionType.THEORETICAL_FRAMEWORK, SectionType.IMPLEMENTATION],
    "result_comparison": [SectionType.RESULTS, SectionType.DISCUSSION, SectionType.EXPERIMENTS],
    "extends": [SectionType.LITERATURE_REVIEW, SectionType.DISCUSSION],
    "contrasts": [SectionType.LITERATURE_REVIEW, SectionType.DISCUSSION],
    "uses_data": [SectionType.DATA_DESCRIPTION, SectionType.METHODOLOGY, SectionType.EXPERIMENTS],
}


def _auto_assign_section(
    intent: str | None, outline: list[dict] | None
) -> str | None:
    """Suggest a section from the outline based on citation intent."""
    if not intent or not outline:
        return None
    target_types = INTENT_TO_SECTION_TYPES.get(intent)
    if not target_types:
        return None
    for section in outline:
        section_type = infer_section_type(section.get("name", ""))
        if section_type and section_type in target_types:
            return section["id"]
    return None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CurateDecision(BaseModel):
    fragment_id: str
    citekey: str
    verdict: str  # "accepted" | "rejected"
    assigned_section: Optional[str] = None
    note: Optional[str] = None


class CurateRequest(BaseModel):
    decisions: list[CurateDecision]


class CurateResponse(BaseModel):
    curated: int
    accepted: int
    rejected: int


class PendingFragment(BaseModel):
    fragment_id: str
    text: str
    citation_intent: str = ""
    fragment_type: str = ""
    page: int | None = None
    citekey: str = ""
    suggested_section: str | None = None


class PendingFragmentsResponse(BaseModel):
    fragments: list[PendingFragment]
    total: int
    curated_count: int


class CuratedFragment(BaseModel):
    fragment_id: str
    citekey: str
    text: str
    citation_intent: str = ""
    assigned_section: str | None = None
    note: str | None = None
    verdict: str = ""
    curated_at: str = ""


class CuratedBankResponse(BaseModel):
    fragments: list[CuratedFragment]
    total: int
    by_section: dict[str, int]


class CurationPatch(BaseModel):
    verdict: Optional[str] = None
    assigned_section: Optional[str] = None
    note: Optional[str] = None


class SuggestFragment(BaseModel):
    fragment_id: str
    text: str
    citation_intent: str = ""
    source: str = ""  # "Author et al., Year"
    citekey: str = ""
    match_reason: str = ""  # "intent_match" | "similarity"
    score: float = 0.0


class GapAlert(BaseModel):
    missing_intents: list[str]
    message: str


class SuggestFragmentsResponse(BaseModel):
    gap_alert: GapAlert | None = None
    suggestions: list[SuggestFragment]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_project_or_404(project_id: str, user: UserRecord) -> dict:
    """Load project and verify ownership."""
    store = get_user_store()
    project = store.get_project_by_id(project_id)
    if not project or project["user_id"] != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _build_fragment_text_map(
    user_id: str, citekeys: set[str]
) -> dict[str, dict]:
    """Build fragment_id → {text, citation_intent, page, citekey} from paper_store."""
    paper_store = get_paper_store()
    library = get_user_library()
    result: dict[str, dict] = {}
    for citekey in citekeys:
        src = library.get_source_by_citekey(citekey, user_id=user_id)
        if not src:
            continue
        fragments = paper_store.get_fragments(src.paper_id)
        for f in fragments:
            result[f.fragment_id] = {
                "text": f.fragment_text,
                "citation_intent": f.citation_intent or "",
                "fragment_type": f.fragment_type or "",
                "page": f.page_number,
                "citekey": citekey,
            }
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{project_id}/fragments/pending", response_model=PendingFragmentsResponse)
async def get_pending_fragments(
    project_id: str,
    citekey: str,
    user: UserRecord = Depends(get_current_user),
) -> PendingFragmentsResponse:
    """Get uncurated fragments for a source in a project."""
    project = _get_project_or_404(project_id, user)
    paper_store = get_paper_store()
    library = get_user_library()
    user_store = get_user_store()
    outline = project.get("outline")

    src = library.get_source_by_citekey(citekey, user_id=user.user_id)
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")

    all_fragments = paper_store.get_fragments(src.paper_id)
    curated_ids = user_store.get_curated_fragment_ids(project_id)

    pending = []
    for f in all_fragments:
        if f.fragment_id not in curated_ids:
            pending.append(PendingFragment(
                fragment_id=f.fragment_id,
                text=f.fragment_text,
                citation_intent=f.citation_intent or "",
                fragment_type=f.fragment_type or "",
                page=f.page_number,
                citekey=citekey,
                suggested_section=_auto_assign_section(f.citation_intent, outline),
            ))

    return PendingFragmentsResponse(
        fragments=pending,
        total=len(all_fragments),
        curated_count=len(curated_ids),
    )


@router.post("/{project_id}/fragments/curate", response_model=CurateResponse)
async def curate_fragments(
    project_id: str,
    body: CurateRequest,
    user: UserRecord = Depends(get_current_user),
) -> CurateResponse:
    """Submit curation decisions (accept/reject) for fragments."""
    project = _get_project_or_404(project_id, user)
    user_store = get_user_store()
    outline = project.get("outline")

    decisions = []
    accepted = 0
    rejected = 0
    for d in body.decisions:
        section = d.assigned_section
        if d.verdict == "accepted" and not section:
            # Try auto-assignment via intent→section_type mapping
            paper_store = get_paper_store()
            library = get_user_library()
            src = library.get_source_by_citekey(d.citekey, user_id=user.user_id)
            intent = None
            if src:
                for f in paper_store.get_fragments(src.paper_id):
                    if f.fragment_id == d.fragment_id:
                        intent = f.citation_intent
                        break
            section = _auto_assign_section(intent, outline)

        decisions.append({
            "fragment_id": d.fragment_id,
            "citekey": d.citekey,
            "verdict": d.verdict,
            "assigned_section": section,
            "note": d.note,
        })
        if d.verdict == "accepted":
            accepted += 1
        else:
            rejected += 1

    count = user_store.curate_fragments(project_id, decisions)
    return CurateResponse(curated=count, accepted=accepted, rejected=rejected)


@router.get("/{project_id}/fragments/curated", response_model=CuratedBankResponse)
async def get_curated_fragments(
    project_id: str,
    verdict: Optional[str] = None,
    section: Optional[str] = None,
    citekey: Optional[str] = None,
    user: UserRecord = Depends(get_current_user),
) -> CuratedBankResponse:
    """Get curated fragments with optional filters."""
    _get_project_or_404(project_id, user)
    user_store = get_user_store()

    curated = user_store.get_curated(
        project_id, verdict=verdict, section=section, citekey=citekey
    )

    # Collect unique citekeys to fetch fragment text
    citekeys = {c["citekey"] for c in curated}
    text_map = _build_fragment_text_map(user.user_id, citekeys)

    fragments = []
    by_section: dict[str, int] = {}
    for c in curated:
        frag_data = text_map.get(c["fragment_id"], {})
        sec = c["assigned_section"] or ""
        by_section[sec] = by_section.get(sec, 0) + 1
        fragments.append(CuratedFragment(
            fragment_id=c["fragment_id"],
            citekey=c["citekey"],
            text=frag_data.get("text", ""),
            citation_intent=frag_data.get("citation_intent", ""),
            assigned_section=c["assigned_section"],
            note=c["note"],
            verdict=c["verdict"],
            curated_at=c["curated_at"] or "",
        ))

    return CuratedBankResponse(
        fragments=fragments,
        total=len(fragments),
        by_section=by_section,
    )


@router.patch("/{project_id}/fragments/curate/{fragment_id}")
async def update_curation(
    project_id: str,
    fragment_id: str,
    body: CurationPatch,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Partial update of a curation decision (verdict, section, note)."""
    _get_project_or_404(project_id, user)
    user_store = get_user_store()

    updated = user_store.update_curation(
        project_id,
        fragment_id,
        verdict=body.verdict,
        assigned_section=body.assigned_section,
        note=body.note,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Curation decision not found")
    return {"ok": True}


@router.get("/{project_id}/fragments/suggest", response_model=SuggestFragmentsResponse)
async def suggest_fragments(
    project_id: str,
    section: str,
    user: UserRecord = Depends(get_current_user),
) -> SuggestFragmentsResponse:
    """Suggest fragments for a section based on intent match and similarity."""
    project = _get_project_or_404(project_id, user)
    user_store = get_user_store()
    library = get_user_library()

    outline = project.get("outline") or []
    # Find the target section's inferred type
    target_section_type = None
    for s in outline:
        if s["id"] == section:
            target_section_type = infer_section_type(s.get("name", ""))
            break

    # Get all accepted fragment IDs for this section (to exclude)
    accepted_in_section = {
        c["fragment_id"]
        for c in user_store.get_curated(project_id, verdict="accepted", section=section)
    }
    # Get all curated IDs (to mark already-decided)
    all_curated_ids = user_store.get_curated_fragment_ids(project_id)

    # Gather all fragments from user's library sources
    all_sources = library.get_all_sources(user_id=user.user_id)
    known_citekeys = {s.citekey for s in all_sources}
    text_map = _build_fragment_text_map(user.user_id, known_citekeys)

    suggestions: list[SuggestFragment] = []
    seen_intents: set[str] = set()

    for frag_id, frag_data in text_map.items():
        # Skip already accepted in this section
        if frag_id in accepted_in_section:
            continue
        # Skip already curated (accepted elsewhere or rejected)
        if frag_id in all_curated_ids:
            continue

        intent = frag_data.get("citation_intent", "")
        match_reason = ""
        score = 0.0

        # Check intent match
        if intent and target_section_type:
            matching_types = INTENT_TO_SECTION_TYPES.get(intent, [])
            if target_section_type in matching_types:
                match_reason = "intent_match"
                score = 1.0

        if not match_reason:
            # Fallback: lower-ranked similarity placeholder
            match_reason = "similarity"
            score = 0.3

        if intent:
            seen_intents.add(intent)

        suggestions.append(SuggestFragment(
            fragment_id=frag_id,
            text=frag_data.get("text", ""),
            citation_intent=intent,
            source=frag_data.get("citekey", ""),
            citekey=frag_data.get("citekey", ""),
            match_reason=match_reason,
            score=score,
        ))

    # Sort: intent_match first (score desc), then similarity
    suggestions.sort(key=lambda s: -s.score)
    suggestions = suggestions[:20]

    # Gap alert: check for missing intents
    gap_alert = None
    if target_section_type:
        expected_intents = {
            intent
            for intent, types in INTENT_TO_SECTION_TYPES.items()
            if target_section_type in types
        }
        accepted_intents = set()
        for c in user_store.get_curated(project_id, verdict="accepted", section=section):
            fdata = text_map.get(c["fragment_id"], {})
            if fdata.get("citation_intent"):
                accepted_intents.add(fdata["citation_intent"])
        missing = list(expected_intents - accepted_intents)
        if missing:
            gap_alert = GapAlert(
                missing_intents=missing,
                message=f"В этом разделе не хватает цитат типа: {', '.join(missing)}",
            )

    return SuggestFragmentsResponse(gap_alert=gap_alert, suggestions=suggestions)
