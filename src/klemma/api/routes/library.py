"""Library endpoints: user's paper collection CRUD (ADR-009, #99)."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time as _time
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user, get_user_store
from ..deps import get_file_store, get_paper_store, get_project_store, get_user_library
from ..recommendations import invalidate_for_user

try:
    from redis import Redis
    from rq import Queue

    _RQ_AVAILABLE = True
except ImportError:
    _RQ_AVAILABLE = False

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SourceResponse(BaseModel):
    """A source in the user's library."""

    citekey: str
    paper_id: str
    status: str
    title: str = ""
    authors: str = ""
    year: int | None = None
    doi: str | None = None
    abstract: str = ""
    chapters: list[int] = []
    sections: list[str] = []


class SourceListResponse(BaseModel):
    """Paginated list of sources."""

    sources: list[SourceResponse]
    total: int


class SourceCreateRequest(BaseModel):
    """Add a source to the library by metadata."""

    citekey: str
    title: str
    authors: str = ""
    year: int | None = None
    doi: str | None = None
    abstract: str = ""
    project_id: str | None = None


class FragmentResponse(BaseModel):
    """A citation fragment from a paper."""

    fragment_id: str
    text: str
    fragment_type: str = "key_idea"
    page_number: int | None = None
    citation_intent: str | None = None


class SourceDetailResponse(SourceResponse):
    """Source with fragments."""

    fragments: list[FragmentResponse] = []


class FragmentSearchResult(BaseModel):
    """A single fragment result from semantic/text search."""

    fragment_id: str
    citekey: str
    title: str
    authors: str = ""
    year: int | None = None
    text: str
    fragment_type: str = "key_idea"


class FragmentSearchResponse(BaseModel):
    """Response from GET /library/fragments/search."""

    results: list[FragmentSearchResult]
    total: int
    query: str


class SectionServed(BaseModel):
    section: str
    count: int


class ReferenceGapResponse(BaseModel):
    """A single reference gap — a paper cited by library sources but not in the library."""

    title: str
    authors: str | None = None
    year: int | None = None
    doi: str | None = None
    cited_by_count: int
    score: float = 0.0
    avg_quality: float = 3.0
    intent_weight: float = 1.0
    semantic_factor: float = 1.0
    intents: list[str] = []
    top_intent: str | None = None
    sections_served: list[SectionServed] = []


class ReferenceGapsResponse(BaseModel):
    """Response from GET /library/gaps."""

    gaps: list[ReferenceGapResponse]
    total: int
    detail: str | None = None


class RecommendationItem(BaseModel):
    """One LLM-curated recommendation."""

    title: str
    authors: str = ""
    year: int | None = None
    doi: str | None = None
    rationale: str = ""
    score: float = 5.0


class RecommendationsResponse(BaseModel):
    """Response from GET /library/recommendations."""

    recommendations: list[RecommendationItem]
    total: int
    model: str = ""
    generated_at: str = ""
    cached: bool = False
    detail: str | None = None
    warning: str | None = None


class MetadataCurrentFields(BaseModel):
    title: str = ""
    authors: str = ""
    year: int | None = None
    doi: str = ""  # empty string when unknown (not None)
    abstract: str = ""

    @classmethod
    def from_paper(cls, paper) -> "MetadataCurrentFields":
        """Build from a PaperRecord, coercing None → empty strings."""
        if paper is None:
            return cls()
        return cls(
            title=paper.title or "",
            authors=paper.authors or "",
            year=paper.year,
            doi=paper.doi or "",
            abstract=paper.abstract or "",
        )


class MetadataPreviewResponse(BaseModel):
    """Response from GET /library/sources/{citekey}/metadata-preview."""

    current: MetadataCurrentFields
    suggested_doi: str | None = None


class EnrichRequest(BaseModel):
    """Request body for POST /library/sources/{citekey}/enrich-metadata."""

    doi: str = ""
    abstract_override: str | None = None


class EnrichResponse(BaseModel):
    """Response from POST /library/sources/{citekey}/enrich-metadata."""

    matched: bool
    source: str  # "doi" | "title" | "timeout" | "none"
    fields: MetadataCurrentFields
    embedding_status: str  # "pending" | "skipped"


# In-memory rate limiter for enrich-metadata: 10 req/min per user
_enrich_rate_limit_store: dict[str, list[float]] = {}


def _check_enrich_rate_limit(user_id: str) -> None:
    """Raise HTTP 429 if user exceeds 10 enrich-metadata requests per minute.

    NOTE: This limiter is **process-local** — it resets on API restart and does
    not synchronize across multiple worker processes. Acceptable for single-worker
    SaaS deployment; replace with Redis-backed rate limiting if workers scale to >1.
    """
    now = _time.monotonic()
    window = 60.0
    max_requests = 10
    timestamps = _enrich_rate_limit_store.get(user_id, [])
    # Evict expired entries
    timestamps = [t for t in timestamps if now - t < window]
    if len(timestamps) >= max_requests:
        raise HTTPException(
            status_code=429,
            detail="Слишком частые запросы — максимум 10 запросов в минуту",
        )
    timestamps.append(now)
    _enrich_rate_limit_store[user_id] = timestamps


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/sources", response_model=SourceListResponse)
async def list_sources(
    user: UserRecord = Depends(get_current_user),
    project_id: str | None = Query(default=None, description="Filter by project"),
    q: str | None = Query(default=None, description="Full-text search on title, authors, citekey"),
) -> SourceListResponse:
    """List sources in the authenticated user's library.

    Optional ``q`` filters by full-text match on title, authors, or citekey.
    """
    library = get_user_library()
    paper_store = get_paper_store()
    project_store = get_project_store()

    all_sources = library.get_all_sources(project_id=project_id, user_id=user.user_id)
    results: list[SourceResponse] = []
    q_lower = q.lower().strip() if q else None
    for src in all_sources:
        paper = paper_store.get_paper_by_id(src.paper_id)
        title = paper.title if paper else ""
        authors = paper.authors if paper else ""
        if q_lower and not (
            q_lower in title.lower()
            or q_lower in authors.lower()
            or q_lower in src.citekey.lower()
        ):
            continue
        project_sections = project_store.get_source_sections(src.citekey, user_id=user.user_id)
        sections = project_sections if project_sections else src.sections
        results.append(
            SourceResponse(
                citekey=src.citekey,
                paper_id=src.paper_id,
                status=src.status,
                title=title,
                authors=authors,
                year=paper.year if paper else None,
                doi=paper.doi if paper else None,
                abstract=paper.abstract if paper else "",
                chapters=src.chapters,
                sections=sections,
            )
        )

    return SourceListResponse(sources=results, total=len(results))


@router.get("/fragments/search", response_model=FragmentSearchResponse)
async def search_fragments(
    q: str = Query(..., min_length=2, description="Search query (minimum 2 characters)"),
    limit: int = Query(default=10, ge=1, le=50, description="Maximum results to return"),
    user: UserRecord = Depends(get_current_user),
) -> FragmentSearchResponse:
    """Search citation fragments across the user's library by text.

    Returns up to *limit* fragments whose text contains the query string,
    ranked by length (shorter = more focused).  Only fragments from papers
    in the authenticated user's library are returned.
    """
    from klemma.stores.paper_store import LocalPaperStore

    paper_store = get_paper_store()
    if not isinstance(paper_store, LocalPaperStore):
        return FragmentSearchResponse(results=[], total=0, query=q)

    raw = paper_store.search_fragments_for_user(user.user_id, q, limit)
    results = [
        FragmentSearchResult(
            fragment_id=r["fragment_id"],
            citekey=r["citekey"],
            title=r["title"],
            authors=r["authors"],
            year=r["year"],
            text=r["text"],
            fragment_type=r["fragment_type"],
        )
        for r in raw
    ]
    return FragmentSearchResponse(results=results, total=len(results), query=q)


@router.get("/sources/{citekey}", response_model=SourceDetailResponse)
async def get_source(
    citekey: str,
    user: UserRecord = Depends(get_current_user),
) -> SourceDetailResponse:
    """Get a source with its fragments. Only accessible if owned by the authenticated user."""
    library = get_user_library()
    paper_store = get_paper_store()

    src = library.get_source_by_citekey(citekey, user_id=user.user_id)
    if src is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{citekey}' not found",
        )

    paper = paper_store.get_paper_by_id(src.paper_id)
    fragments = paper_store.get_fragments(src.paper_id)

    return SourceDetailResponse(
        citekey=src.citekey,
        paper_id=src.paper_id,
        status=src.status,
        title=paper.title if paper else "",
        authors=paper.authors if paper else "",
        year=paper.year if paper else None,
        doi=paper.doi if paper else None,
        abstract=paper.abstract if paper else "",
        chapters=src.chapters,
        sections=src.sections,
        fragments=[
            FragmentResponse(
                fragment_id=f.fragment_id,
                text=f.fragment_text,
                fragment_type=f.fragment_type,
                page_number=f.page_number,
                citation_intent=f.citation_intent,
            )
            for f in fragments
        ],
    )


@router.post("/sources", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def add_source(
    body: SourceCreateRequest,
    user: UserRecord = Depends(get_current_user),
) -> SourceResponse:
    """Add a source to the authenticated user's library (metadata only, no PDF upload)."""
    library = get_user_library()
    paper_store = get_paper_store()

    existing = library.get_source_by_citekey(body.citekey, user_id=user.user_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Source '{body.citekey}' already exists",
        )

    # Register paper in global corpus (dedup by DOI if available)
    paper = None
    if body.doi:
        paper = paper_store.find_paper(doi=body.doi)

    if paper:
        paper_id = paper.paper_id
    else:
        paper_id = paper_store.register_paper(
            title=body.title,
            authors=body.authors,
            year=body.year,
            doi=body.doi,
            abstract=body.abstract,
            pdf_hash="",  # No PDF uploaded yet
        )

    library.add_source(
        paper_id, body.citekey,
        status="pending",
        project_id=body.project_id,
        user_id=user.user_id,
    )
    invalidate_for_user(paper_store, user.user_id)

    return SourceResponse(
        citekey=body.citekey,
        paper_id=paper_id,
        status="pending",
        title=body.title,
        authors=body.authors,
        year=body.year,
        doi=body.doi,
        abstract=body.abstract,
    )


@router.delete("/sources/{citekey}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_source(
    citekey: str,
    user: UserRecord = Depends(get_current_user),
):
    """Remove a source from the authenticated user's library.

    Does NOT delete the paper from the global corpus (other users may reference it).
    Returns 404 if the source doesn't exist or belongs to another user.
    """
    library = get_user_library()

    src = library.get_source_by_citekey(citekey, user_id=user.user_id)
    if src is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{citekey}' not found",
        )

    library.remove_source(citekey, user_id=user.user_id)
    invalidate_for_user(get_paper_store(), user.user_id)


class UploadResponse(BaseModel):
    """Response from PDF upload."""

    citekey: str
    paper_id: str
    pdf_hash: str
    status: str
    deduplicated: bool = False
    already_owned: bool = False  # True when user already had this source
    job_id: str | None = None  # Set when auto-processing is enqueued


MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_pdf(
    file: UploadFile,
    user: UserRecord = Depends(get_current_user),
    project_id: str | None = Form(default=None),
) -> UploadResponse:
    """Upload a PDF and register it in the authenticated user's library.

    Deduplicates by pdf_hash: if the same PDF already exists in the global
    corpus, reuses the existing paper_id (no re-extraction needed).
    Generates a citekey from the filename.
    """
    is_pdf = (
        (file.filename and file.filename.lower().endswith(".pdf"))
        or (file.content_type and file.content_type == "application/pdf")
    )
    if not file.filename or not is_pdf:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only PDF files are accepted (got: {file.filename!r}, type: {file.content_type!r})",
        )

    data = await file.read()
    if len(data) < 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File too small to be a valid PDF",
        )
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {MAX_PDF_BYTES // (1024 * 1024)} MB limit",
        )

    pdf_hash = hashlib.sha256(data).hexdigest()
    try:
        paper_store = get_paper_store()
        file_store = get_file_store()
        library = get_user_library()
    except Exception as exc:
        logger.exception("Failed to get stores for upload")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Upload failed: store init error: {type(exc).__name__}: {exc}",
        )

    # Dedup: check if this PDF already exists in the global corpus
    existing = paper_store.find_paper(pdf_hash=pdf_hash)
    if existing:

        # If the user already has this paper, return the existing citekey unchanged.
        # This preserves citekey stability: re-uploading the same PDF does not create
        # a new citekey and does not break [@citekey] references in draft files.
        existing_source = library.get_source_by_paper_id(
            existing.paper_id, user_id=user.user_id
        )
        if existing_source:
            return UploadResponse(
                citekey=existing_source.citekey,
                paper_id=existing.paper_id,
                pdf_hash=pdf_hash,
                status=existing_source.status,
                deduplicated=True,
                already_owned=True,
            )
        # New user, same PDF — generate citekey from filename
        base = _citekey_from_filename(file.filename)
        citekey = _resolve_citekey_collision(library, base, user.user_id, pdf_hash)

        library.add_source(
            existing.paper_id, citekey,
            status="completed",
            project_id=project_id,
            user_id=user.user_id,
        )
        invalidate_for_user(paper_store, user.user_id)
        return UploadResponse(
            citekey=citekey,
            paper_id=existing.paper_id,
            pdf_hash=pdf_hash,
            status="completed",
            deduplicated=True,
        )

    # New paper: register + store file
    try:
        base = _citekey_from_filename(file.filename)
        citekey = _resolve_citekey_collision(library, base, user.user_id, pdf_hash)

        paper_id = paper_store.register_paper(
            title=file.filename.rsplit(".", 1)[0],
            pdf_hash=pdf_hash,
        )
        safe_filename = re.sub(r"[^\w.\-]", "_", file.filename)
        try:
            file_store.save(paper_id, data, safe_filename)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

        library.add_source(
            paper_id, citekey,
            status="pending",
            project_id=project_id,
            user_id=user.user_id,
        )
        invalidate_for_user(paper_store, user.user_id)

        job_id = _enqueue_processing(paper_id, citekey, user.user_id, project_id)

        return UploadResponse(
            citekey=citekey,
            paper_id=paper_id,
            pdf_hash=pdf_hash,
            status="queued" if job_id else "pending",
            job_id=job_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Upload failed for %r (user=%s)", file.filename, user.user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Upload failed: {type(exc).__name__}: {exc}",
        )


@router.get("/sources/{citekey}/metadata-preview", response_model=MetadataPreviewResponse)
async def metadata_preview(
    citekey: str,
    user: UserRecord = Depends(get_current_user),
) -> MetadataPreviewResponse:
    """Return current metadata fields + DOI suggestion from PDF text regex.

    Used to pre-fill the MetadataEnrichDialog on SourceView.
    """
    library = get_user_library()
    paper_store = get_paper_store()
    file_store = get_file_store()

    # Ownership check
    paper_id = library.resolve_paper_id(citekey, user_id=user.user_id)
    if not paper_id:
        raise HTTPException(status_code=404, detail=f"Source '{citekey}' not found")

    paper = paper_store.get_paper_by_id(paper_id)
    current = MetadataCurrentFields.from_paper(paper)

    # Try to extract DOI from PDF text
    suggested_doi: str | None = None
    try:
        from klemma.literature.metadata import _extract_doi_from_text
        from klemma.literature.pdf import PDFExtractor

        paper_dir = file_store.get_paper_dir(paper_id)
        pdf_files = list(paper_dir.glob("*.pdf")) if paper_dir.is_dir() else []
        if pdf_files:
            extractor = PDFExtractor(max_chars=3000)
            text = extractor.extract(pdf_files[0]) or ""
            doi = _extract_doi_from_text(text)
            if doi:
                suggested_doi = doi
    except Exception as exc:
        logger.warning("DOI extraction for preview failed for %s (non-fatal): %s", citekey, exc)

    return MetadataPreviewResponse(current=current, suggested_doi=suggested_doi)


@router.post("/sources/{citekey}/enrich-metadata", response_model=EnrichResponse)
async def enrich_metadata(
    citekey: str,
    body: EnrichRequest,
    user: UserRecord = Depends(get_current_user),
) -> EnrichResponse:
    """Enrich a source with metadata from CrossRef.

    Uses DOI for exact lookup if provided; falls back to title-based search.
    After enrichment, re-embed job is enqueued asynchronously.
    Rate-limited to 10 requests/min per user.
    """
    _check_enrich_rate_limit(user.user_id)

    library = get_user_library()
    paper_store = get_paper_store()

    # Ownership check
    paper_id = library.resolve_paper_id(citekey, user_id=user.user_id)
    if not paper_id:
        raise HTTPException(status_code=404, detail=f"Source '{citekey}' not found")

    paper = paper_store.get_paper_by_id(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail=f"Paper record for '{citekey}' not found")

    from klemma.literature.metadata import lookup_crossref, lookup_crossref_by_doi

    meta: dict | None = None
    lookup_source = "none"

    doi = (body.doi or "").strip()
    if doi:
        meta = lookup_crossref_by_doi(doi, timeout=10)
        if meta:
            lookup_source = "doi"
        else:
            lookup_source = "none"
    elif paper.title:
        try:
            meta = lookup_crossref(paper.title, timeout=5)
            if meta:
                lookup_source = "title"
        except Exception:
            lookup_source = "timeout"
            meta = None

    if meta is None and lookup_source not in ("timeout",):
        lookup_source = "none"

    # Apply abstract_override if user provided it
    if body.abstract_override and body.abstract_override.strip():
        if meta is None:
            meta = {}
        meta["abstract"] = body.abstract_override.strip()

    # Persist enriched fields
    if meta:
        update_kwargs: dict = {}
        if meta.get("title"):
            update_kwargs["title"] = meta["title"]
        if meta.get("authors"):
            update_kwargs["authors"] = meta["authors"]
        if meta.get("year"):
            update_kwargs["year"] = meta["year"]
        if meta.get("doi"):
            # Log if DOI collision — two paper_ids with same DOI (V1 policy: allow)
            existing_doi_paper = paper_store.find_paper(doi=meta["doi"])
            if existing_doi_paper and existing_doi_paper.paper_id != paper_id:
                logger.warning(
                    "DOI collision: %s already assigned to paper_id %s, also assigning to %s",
                    meta["doi"], existing_doi_paper.paper_id, paper_id,
                )
            update_kwargs["doi"] = meta["doi"]
        if meta.get("abstract"):
            update_kwargs["abstract"] = meta["abstract"]
        if update_kwargs:
            paper_store.update_paper_metadata(paper_id, **update_kwargs)
            logger.info("Metadata enriched for %s via %s: %s", citekey, lookup_source, list(update_kwargs.keys()))

    # Re-embed asynchronously (non-blocking)
    embedding_status = "skipped"
    try:
        _enqueue_re_embed(paper_id, citekey, user.user_id)
        embedding_status = "pending"
    except Exception as exc:
        logger.warning("Re-embed enqueue failed for %s (non-fatal): %s", citekey, exc)

    # Build response from updated paper record
    updated_paper = paper_store.get_paper_by_id(paper_id)
    fields = MetadataCurrentFields.from_paper(updated_paper)
    return EnrichResponse(
        matched=meta is not None,
        source=lookup_source,
        fields=fields,
        embedding_status=embedding_status,
    )


def _enqueue_re_embed(paper_id: str, citekey: str, user_id: str) -> None:
    """Enqueue a lightweight re-embedding job for a paper after metadata enrichment."""
    if not _RQ_AVAILABLE:
        return
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_conn = Redis.from_url(redis_url)
    q = Queue(connection=redis_conn)
    from ..tasks import re_embed_source_task
    data_dir = os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma"))
    q.enqueue(re_embed_source_task, paper_id, citekey, data_dir, job_timeout=120)


@router.get("/gaps", response_model=ReferenceGapsResponse)
async def list_reference_gaps(
    user: UserRecord = Depends(get_current_user),
) -> ReferenceGapsResponse:
    """Return reference gaps — papers cited by library sources but not in the library.

    Gaps scored by: count × avg_quality × intent_weight × semantic_factor
    (Teufel 2006 citation intent taxonomy; semantic_factor is a noise penalty).
    """
    from ..recommendations import apply_recency_filter, compute_scored_gaps

    paper_store = get_paper_store()
    library = get_user_library()
    project_store = get_project_store()

    user_sources = library.get_all_sources(user_id=user.user_id)
    if len(user_sources) < 3:
        return ReferenceGapsResponse(
            gaps=[],
            total=0,
            detail="Загрузите больше источников (минимум 3) для анализа пробелов",
        )

    scored_gaps = compute_scored_gaps(
        paper_store=paper_store,
        library=library,
        project_store=project_store,
        user_id=user.user_id,
        limit=200,
    )
    if not scored_gaps:
        return ReferenceGapsResponse(gaps=[], total=0)

    filtered = apply_recency_filter(scored_gaps)
    gaps = filtered[:10]

    gap_responses = [
        ReferenceGapResponse(
            title=g.get("title", ""),
            authors=g.get("authors") or None,
            year=g.get("year"),
            doi=g.get("doi"),
            cited_by_count=g.get("cited_by_count", 0),
            score=g.get("score", 0.0),
            avg_quality=g.get("avg_quality", 3.0),
            intent_weight=g.get("intent_weight", 1.0),
            semantic_factor=g.get("semantic_factor", 1.0),
            intents=g.get("intents", []),
            top_intent=g.get("top_intent"),
            sections_served=[
                SectionServed(section=s["section"], count=s["count"])
                for s in g.get("sections_served", [])
            ],
        )
        for g in gaps
    ]

    return ReferenceGapsResponse(gaps=gap_responses, total=len(gap_responses))


# ---------------------------------------------------------------------------
# LLM-curated recommendations (#331)
# ---------------------------------------------------------------------------


@router.get("/recommendations", response_model=RecommendationsResponse)
async def list_recommendations(
    project_id: str = Query(..., description="Project ID scoping the outline context"),
    user: UserRecord = Depends(get_current_user),
) -> RecommendationsResponse:
    """LLM-curated top-10 library recommendations with rationales.

    Reuses the ``/library/gaps`` scoring pool (`compute_scored_gaps`, top-50,
    pre-recency), then asks an LLM to pick 10 recommendations with 1–2
    sentence rationale each, grounded in the project outline and 3–5 loaded
    source abstracts. Cached by
    ``(user_id, project_id, library_state_hash, outline_hash, model)``.

    On LLM error, falls back to recency-filtered top-10 with empty rationales
    and ``detail = "AI недоступен — базовая сортировка"`` (HTTP 200).
    """
    import json
    from datetime import datetime, timezone

    from jinja2.sandbox import SandboxedEnvironment

    from ..recommendations import (
        CANDIDATE_LIMIT,
        LOADED_SOURCES_LIMIT,
        apply_recency_filter,
        build_prompt_inputs,
        compute_library_state_hash,
        compute_outline_hash,
        compute_scored_gaps,
        detect_rationale_language,
        parse_llm_output,
        select_loaded_sources,
    )
    from ..tasks import _create_ai_provider

    paper_store = get_paper_store()
    library = get_user_library()
    project_store = get_project_store()
    user_store = get_user_store()

    # Ownership check
    project = user_store.get_project_by_id(project_id)
    if not project or project["user_id"] != user.user_id:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    # Token quota gate — same guard `process_source` uses. Without this,
    # a user can call /library/recommendations repeatedly to deplete the
    # Anthropic bill while bypassing the per-user KLEMMA_INITIAL_TOKEN_GRANT
    # budget that upload/draft paths respect.
    if not user_store.check_token_limit(user.user_id):
        raise HTTPException(
            status_code=429,
            detail="Лимит токенов исчерпан — обратитесь к администратору",
        )

    # Project-scoped source set (attached to this project OR unassigned).
    # Matches LocalUserLibrary.get_all_sources() project_id semantics so that
    # users with multiple projects don't see project A's sources influence
    # project B's recommendations.
    user_sources = library.get_all_sources(
        user_id=user.user_id, project_id=project_id
    )
    if len(user_sources) < 3:
        return RecommendationsResponse(
            recommendations=[],
            total=0,
            detail="Загрузите больше источников (минимум 3) для рекомендаций",
        )

    outline = project.get("outline") or []
    project_name = project.get("name", "") or ""

    # Cache probe (resolve active model first — cache key includes it)
    try:
        ai, ai_cfg = _create_ai_provider()
    except Exception as exc:
        logger.warning("AI provider unavailable for recommendations: %s", exc)
        ai, ai_cfg = None, None
    model = getattr(ai_cfg, "model", "") if ai_cfg else ""

    library_state_hash = compute_library_state_hash(user_sources)
    outline_hash = compute_outline_hash(outline)

    warning: str | None = None
    if not outline:
        warning = "Задайте outline проекта для более точных рекомендаций"

    if ai is not None and model:
        cached = paper_store.get_cached_recommendations(
            user_id=user.user_id,
            project_id=project_id,
            library_state_hash=library_state_hash,
            outline_hash=outline_hash,
            model=model,
        )
        if cached is not None:
            try:
                items = json.loads(cached["json_result"])
            except json.JSONDecodeError:
                items = []
            return RecommendationsResponse(
                recommendations=[RecommendationItem(**it) for it in items],
                total=len(items),
                model=cached["model"],
                generated_at=cached["created_at"],
                cached=True,
                warning=warning,
            )

    # Build candidate pool (scored, without recency filter), scoped by project
    candidates = compute_scored_gaps(
        paper_store=paper_store,
        library=library,
        project_store=project_store,
        user_id=user.user_id,
        project_id=project_id,
        limit=CANDIDATE_LIMIT,
    )
    if not candidates:
        return RecommendationsResponse(
            recommendations=[],
            total=0,
            detail="Пока нет кандидатов — обработайте больше источников",
            warning=warning,
        )

    loaded_sources = select_loaded_sources(
        paper_store=paper_store,
        library=library,
        user_id=user.user_id,
        project_id=project_id,
        max_items=LOADED_SOURCES_LIMIT,
    )
    if len(loaded_sources) < 3 and warning is None:
        warning = "Для лучшего wow-эффекта обработайте больше источников"

    rationale_language = detect_rationale_language(project_name)
    ctx = build_prompt_inputs(
        project_name=project_name,
        outline=outline,
        loaded_sources=loaded_sources,
        candidates=candidates,
        rationale_language=rationale_language,
    )

    # Fallback branch — no AI: return recency-filtered top-10 with empty rationale
    if ai is None:
        filtered = apply_recency_filter(candidates)[:10]
        items = [
            {
                "title": g.get("title", ""),
                "authors": g.get("authors") or "",
                "year": g.get("year"),
                "doi": g.get("doi"),
                "rationale": "",
                "score": min(10.0, max(1.0, float(g.get("score", 5.0)))),
            }
            for g in filtered
        ]
        return RecommendationsResponse(
            recommendations=[RecommendationItem(**it) for it in items],
            total=len(items),
            model="",
            generated_at=datetime.now(timezone.utc).isoformat(),
            cached=False,
            detail="AI недоступен — базовая сортировка",
            warning=warning,
        )

    # LLM call — resolve prompts with system-home override support.
    # ``resolve_prompt(name, get_system_home())`` honors ``~/.klemma/prompts/``
    # overrides and falls back to the shipped template bundled with the package.
    env = SandboxedEnvironment()
    from klemma.config import get_system_home, resolve_prompt
    try:
        system_path = resolve_prompt(
            "library_recommendations_system.md", get_system_home()
        )
        user_path = resolve_prompt(
            "library_recommendations_user.md", get_system_home()
        )
        system_raw = system_path.read_text(encoding="utf-8")
        user_raw = user_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        logger.error("Recommendation prompt missing: %s", exc)
        return RecommendationsResponse(
            recommendations=[],
            total=0,
            detail="Шаблон промпта не найден",
            warning=warning,
        )
    system_prompt = env.from_string(system_raw).render(**ctx)
    user_prompt = env.from_string(user_raw).render(**ctx)

    llm_out = None
    try:
        llm_out = ai.call_json(
            system=system_prompt,
            user=user_prompt,
            max_tokens=2048,
            temperature=0.3,
            timeout=30,
        )
    except Exception as exc:
        logger.warning("Recommendations LLM call failed: %s", exc)

    parsed = parse_llm_output(llm_out)
    generated_at = datetime.now(timezone.utc).isoformat()

    # Token usage bookkeeping. `call_json` does not return per-call token
    # counts, so we approximate from string lengths (~4 chars/token for
    # English+Russian mix). Only recorded on a successful parse — empty /
    # errored calls don't charge the user. Accuracy ±20% is good enough
    # for a 1M-token budget. Precise metering is a follow-up (#M2 refactor
    # of ai_litellm.call_json to return AICallResult).
    if parsed and llm_out is not None:
        try:
            approx_input = (len(system_prompt) + len(user_prompt)) // 4
            approx_output = len(json.dumps(llm_out, ensure_ascii=False)) // 4
            user_store.record_usage(
                user_id=user.user_id,
                operation="library_recommendations",
                model=model,
                input_tokens=approx_input,
                output_tokens=approx_output,
            )
        except Exception as exc:  # pragma: no cover — non-fatal
            logger.warning("record_usage failed for recommendations: %s", exc)

    if not parsed:
        filtered = apply_recency_filter(candidates)[:10]
        items = [
            {
                "title": g.get("title", ""),
                "authors": g.get("authors") or "",
                "year": g.get("year"),
                "doi": g.get("doi"),
                "rationale": "",
                "score": min(10.0, max(1.0, float(g.get("score", 5.0)))),
            }
            for g in filtered
        ]
        return RecommendationsResponse(
            recommendations=[RecommendationItem(**it) for it in items],
            total=len(items),
            model=model,
            generated_at=generated_at,
            cached=False,
            detail="AI вернул пустой результат — базовая сортировка",
            warning=warning,
        )

    # Cap at 10 per spec
    items = parsed[:10]

    try:
        paper_store.save_cached_recommendations(
            user_id=user.user_id,
            project_id=project_id,
            library_state_hash=library_state_hash,
            outline_hash=outline_hash,
            model=model,
            json_result=json.dumps(items, ensure_ascii=False),
        )
    except Exception as exc:  # pragma: no cover — non-fatal
        logger.warning("Recommendations cache write failed: %s", exc)

    return RecommendationsResponse(
        recommendations=[RecommendationItem(**it) for it in items],
        total=len(items),
        model=model,
        generated_at=generated_at,
        cached=False,
        warning=warning,
    )


def _enqueue_processing(paper_id: str, citekey: str, user_id: str, project_id: str | None = None) -> str | None:
    """Enqueue a process_source job. Returns job_id or None if Redis unavailable."""
    if not _RQ_AVAILABLE:
        return None
    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        redis_conn = Redis.from_url(redis_url)
        q = Queue(connection=redis_conn)
        from ..tasks import process_source
        data_dir = os.environ.get("KLEMMA_DATA_DIR", str(Path.home() / ".klemma"))
        job = q.enqueue(process_source, paper_id, citekey, data_dir, user_id, project_id, job_timeout=300)
        return job.id
    except Exception as exc:
        logger.warning("Auto-processing enqueue failed for %s: %s", citekey, exc)
        return None


def _clean_author_slug(raw: str) -> str:
    """Transliterate Cyrillic/diacritics → Latin, lowercase, strip to ``[a-z0-9]``.

    Used by ``_citekey_from_filename`` to produce a BBT-compatible author
    surname slug. Returns empty string if nothing usable remains.
    """
    if not raw:
        return ""
    from klemma.utils.translit import transliterate_ru

    transliterated = transliterate_ru(raw)
    return re.sub(r"[^a-z0-9]", "", transliterated.lower())


def _resolve_citekey_collision(library, base: str, user_id: str, pdf_hash: str) -> str:
    """Find an unused citekey given ``base`` (e.g. ``smith2023``) for the user.

    BBT-style suffix sequence: ``base``, ``basea``, ``baseb`` … ``basez``.
    If all 27 slots are taken (two authors + 26 disambiguators, which would
    mean 27 papers by the same first author in the same year), falls back to
    ``base_{pdf_hash[:6]}`` so we never loop forever and never clash.
    """
    if not library.get_source_by_citekey(base, user_id=user_id):
        return base
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        candidate = f"{base}{suffix}"
        if not library.get_source_by_citekey(candidate, user_id=user_id):
            return candidate
    # Cosmic-ray territory — fall back to hash suffix for guaranteed uniqueness.
    return f"{base}_{pdf_hash[:6]}"


def _citekey_from_filename(filename: str) -> str:
    """Generate a BBT-style citekey from a PDF filename.

    Format: ``{lastname_lat}{year}``. Cyrillic surnames are transliterated
    via ``transliterate_ru``; the title is NOT included in the slug (BBT
    default is short and deterministic; title slugs were the primary source
    of the long Cyrillic keys seen in prod).

    Collision handling lives at the caller (upload_pdf appends
    ``_{pdf_hash[:6]}`` when the base is already taken).

    Examples:
        'Воронина - 2023 - Основные направления.pdf' → 'voronina2023'
        'Andersson et al. - 2021 - Seasonal Arctic sea ice.pdf' → 'andersson2021'
        'Smith_2020_Machine_Learning.pdf' → 'smith2020'
        'Иванов 2019.pdf' → 'ivanov2019'
    """
    name = filename.rsplit(".", 1)[0]  # remove .pdf

    # Try to parse "Author(s) - Year - Title" format (common from Zotero/Mendeley exports)
    m = re.match(r"^(.+?)\s*[-–—]\s*(\d{4})\s*[-–—]\s*(.+)$", name)
    if m:
        author_part = m.group(1).strip()
        year = m.group(2)
        first_author_raw = re.split(r"[,\s]", author_part)[0]
        first_author = _clean_author_slug(first_author_raw)
        return f"{first_author}{year}" if first_author else f"paper{year}"

    # Fallback: split on separators, extract year if present
    parts = re.split(r"[_\-\s]+", name)
    parts = [p for p in parts if p]
    if not parts:
        return "unknown"

    year = ""
    for p in parts:
        if re.match(r"^\d{4}$", p):
            year = p
            break

    first_author = _clean_author_slug(parts[0]) if parts else ""
    # A digit-only "surname" means the filename started with the year — not a
    # real author. Fall back to "paper" so we don't emit "20232023".
    if not first_author or first_author.isdigit():
        first_author = "paper"

    return f"{first_author}{year}" if year else first_author
