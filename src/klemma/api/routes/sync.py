"""Sync endpoints: library bulk transfer between klemma-cli and server.

All file sync (draft/*.md) goes through the /projects/{id}/drafts API.
No server-side git — local git is the user's own business.
"""

from __future__ import annotations

import base64
import logging
import struct
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from klemma.models import UserRecord

from ..auth.deps import get_current_user
from ..deps import get_paper_store, get_project_store, get_user_library

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas — Library bulk sync
# ---------------------------------------------------------------------------


class SourcePush(BaseModel):
    citekey: str
    paper_id: str = ""
    title: str = ""
    authors: str = ""
    year: Optional[int] = None
    doi: Optional[str] = None
    abstract: str = ""
    sections: list[str] = []
    status: str = "pending"


class FragmentPush(BaseModel):
    fragment_id: str
    paper_id: str
    text: str
    fragment_type: str = "key_idea"
    citation_intent: Optional[str] = None
    page: Optional[int] = None
    verbatim: bool = False


class LibraryPushRequest(BaseModel):
    sources: list[SourcePush] = Field(default=[], max_length=1000)
    fragments: list[FragmentPush] = Field(default=[], max_length=10000)


class EmbeddingEntry(BaseModel):
    id: str
    vector_b64: str
    model: str = "specter2"


class EmbeddingsPushRequest(BaseModel):
    paper_embeddings: list[EmbeddingEntry] = Field(default=[], max_length=500)
    fragment_embeddings: list[EmbeddingEntry] = Field(default=[], max_length=5000)


class DecisionPush(BaseModel):
    decision_id: str = ""
    trigger_type: str = ""
    trigger_source: str = ""
    context_json: str = "{}"
    options_json: str = "{}"
    chosen_option: Optional[str] = None
    rationale: str = ""
    note: str = ""
    feedback: str = ""


class DecisionsPushRequest(BaseModel):
    decisions: list[DecisionPush] = []


class SourcePull(BaseModel):
    citekey: str
    paper_id: str
    title: str = ""
    authors: str = ""
    year: Optional[int] = None
    doi: Optional[str] = None
    abstract: str = ""
    sections: list[str] = []
    status: str = "pending"
    updated_at: str = ""


class FragmentPull(BaseModel):
    fragment_id: str
    paper_id: str
    text: str
    fragment_type: str = "key_idea"
    citation_intent: Optional[str] = None
    page: Optional[int] = None
    verbatim: bool = False


class LibraryPullResponse(BaseModel):
    sources: list[SourcePull] = []
    fragments: list[FragmentPull] = []


class SyncStatusResponse(BaseModel):
    project_id: str
    source_count: int
    fragment_count: int


# ---------------------------------------------------------------------------
# Library bulk sync endpoints
# ---------------------------------------------------------------------------


@router.post("/push/library")
async def push_library(
    body: LibraryPushRequest,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Batch upsert sources + fragments from CLI to server."""
    paper_store = get_paper_store()
    library = get_user_library()
    project_store = get_project_store()

    sources_saved = 0
    fragments_saved = 0

    # Map client paper_id → server paper_id (server may assign new UUIDs)
    paper_id_map: dict[str, str] = {}

    for src in body.sources:
        client_paper_id = src.paper_id
        server_paper_id = client_paper_id

        if not client_paper_id:
            existing = None
            if src.doi:
                existing = paper_store.find_paper(doi=src.doi)
            if existing:
                server_paper_id = existing.paper_id
            else:
                server_paper_id = paper_store.register_paper(
                    title=src.title, authors=src.authors,
                    year=src.year, doi=src.doi, abstract=src.abstract,
                    pdf_hash="",
                )
        else:
            existing = paper_store.get_paper_by_id(client_paper_id)
            if not existing:
                server_paper_id = paper_store.register_paper(
                    title=src.title, authors=src.authors,
                    year=src.year, doi=src.doi, abstract=src.abstract,
                    pdf_hash="",
                )
            else:
                server_paper_id = existing.paper_id
                paper_store.update_paper_metadata(
                    server_paper_id, title=src.title, authors=src.authors,
                    year=src.year, doi=src.doi, abstract=src.abstract,
                )

        if client_paper_id:
            paper_id_map[client_paper_id] = server_paper_id

        library.add_source(
            server_paper_id, src.citekey,
            status=src.status,
            user_id=user.user_id,
        )

        if src.sections:
            project_store.set_source_sections(
                src.citekey, server_paper_id, src.sections, [],
                user_id=user.user_id,
            )

        sources_saved += 1

    for frag in body.fragments:
        from klemma.models import FragmentRecord
        resolved_paper_id = paper_id_map.get(frag.paper_id, frag.paper_id)
        record = FragmentRecord(
            fragment_id=frag.fragment_id,
            paper_id=resolved_paper_id,
            fragment_text=frag.text,
            fragment_type=frag.fragment_type,
            page_number=frag.page,
            citation_intent=frag.citation_intent,
            verbatim=frag.verbatim,
        )
        paper_store.save_fragments(
            resolved_paper_id, [record],
            prompt_hash="cli-sync",
            ai_model="cli-sync",
        )
        fragments_saved += 1

    return {
        "sources_saved": sources_saved,
        "fragments_saved": fragments_saved,
    }


@router.post("/push/embeddings")
async def push_embeddings(
    body: EmbeddingsPushRequest,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Batch upsert embedding vectors (base64-encoded float32)."""
    paper_store = get_paper_store()
    paper_count = 0
    fragment_count = 0

    for emb in body.paper_embeddings:
        vector = _decode_vector(emb.vector_b64)
        paper_store.save_paper_embedding(emb.id, vector, emb.model)
        paper_count += 1

    for emb in body.fragment_embeddings:
        vector = _decode_vector(emb.vector_b64)
        paper_store.save_fragment_embedding(emb.id, vector, emb.model)
        fragment_count += 1

    return {
        "paper_embeddings_saved": paper_count,
        "fragment_embeddings_saved": fragment_count,
    }


@router.post("/push/decisions")
async def push_decisions(
    body: DecisionsPushRequest,
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Batch upsert decisions from CLI (storage deferred to Phase 3)."""
    count = len(body.decisions)
    logger.info("Received %d decisions from user %s (storage deferred)", count, user.user_id)
    return {"decisions_received": count, "status": "acknowledged"}


@router.get("/pull/library", response_model=LibraryPullResponse)
async def pull_library(
    user: UserRecord = Depends(get_current_user),
    since: Optional[str] = Query(default=None, description="ISO 8601 timestamp for incremental pull"),
) -> LibraryPullResponse:
    """Pull library data (sources + fragments) from server to CLI."""
    library = get_user_library()
    paper_store = get_paper_store()

    all_sources = library.get_all_sources(user_id=user.user_id, since=since)
    project_store = get_project_store()

    sources = []
    fragments = []

    for src in all_sources:
        paper = paper_store.get_paper_by_id(src.paper_id)
        project_sections = project_store.get_source_sections(
            src.citekey, user_id=user.user_id
        )
        sections = project_sections if project_sections else src.sections

        sources.append(SourcePull(
            citekey=src.citekey,
            paper_id=src.paper_id,
            title=paper.title if paper else "",
            authors=paper.authors if paper else "",
            year=paper.year if paper else None,
            doi=paper.doi if paper else None,
            abstract=paper.abstract if paper else "",
            sections=sections,
            status=src.status,
            updated_at=datetime.now(timezone.utc).isoformat(),
        ))

        if paper:
            for frag in paper_store.get_fragments(src.paper_id):
                fragments.append(FragmentPull(
                    fragment_id=frag.fragment_id,
                    paper_id=frag.paper_id,
                    text=frag.fragment_text,
                    fragment_type=frag.fragment_type,
                    citation_intent=frag.citation_intent,
                    page=frag.page_number,
                    verbatim=frag.verbatim,
                ))

    return LibraryPullResponse(sources=sources, fragments=fragments)


@router.get("/pull/decisions")
async def pull_decisions(
    user: UserRecord = Depends(get_current_user),
    since: Optional[str] = Query(default=None),
) -> dict:
    """Pull decisions from server (Phase 3 — stub)."""
    return {"decisions": []}


@router.get("/status/{project_id}", response_model=SyncStatusResponse)
async def sync_status(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
) -> SyncStatusResponse:
    """Library counts for the authenticated user."""
    library = get_user_library()
    paper_store = get_paper_store()

    source_count = library.count(user_id=user.user_id)
    all_sources = library.get_all_sources(user_id=user.user_id)
    fragment_count = sum(
        len(paper_store.get_fragments(s.paper_id)) for s in all_sources
    )

    return SyncStatusResponse(
        project_id=project_id,
        source_count=source_count,
        fragment_count=fragment_count,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_vector(b64: str) -> list[float]:
    """Decode base64-encoded float32 vector."""
    raw = base64.b64decode(b64)
    count = len(raw) // 4
    return list(struct.unpack(f"{count}f", raw))
