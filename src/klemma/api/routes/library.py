"""Library endpoints: user's paper collection CRUD (ADR-009, #99)."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user
from ..deps import get_file_store, get_paper_store, get_project_store, get_user_library

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


class UploadResponse(BaseModel):
    """Response from PDF upload."""

    citekey: str
    paper_id: str
    pdf_hash: str
    status: str
    deduplicated: bool = False
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
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted",
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
    paper_store = get_paper_store()
    file_store = get_file_store()
    library = get_user_library()

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
            )
        # New user, same PDF — generate citekey from filename
        citekey = _citekey_from_filename(file.filename)
        if library.get_source_by_citekey(citekey, user_id=user.user_id):
            citekey = f"{citekey}_{pdf_hash[:6]}"
        library.add_source(
            existing.paper_id, citekey,
            status="completed",
            project_id=project_id,
            user_id=user.user_id,
        )
        return UploadResponse(
            citekey=citekey,
            paper_id=existing.paper_id,
            pdf_hash=pdf_hash,
            status="completed",
            deduplicated=True,
        )

    # New paper: register + store file
    citekey = _citekey_from_filename(file.filename)
    if library.get_source_by_citekey(citekey, user_id=user.user_id):
        citekey = f"{citekey}_{pdf_hash[:6]}"

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

    job_id = _enqueue_processing(paper_id, citekey, user.user_id, project_id)

    return UploadResponse(
        citekey=citekey,
        paper_id=paper_id,
        pdf_hash=pdf_hash,
        status="queued" if job_id else "pending",
        job_id=job_id,
    )


@router.get("/gaps")
async def list_reference_gaps(
    user: UserRecord = Depends(get_current_user),
) -> dict:
    """Return reference gaps — papers cited by library sources but not in the library."""
    paper_store = get_paper_store()
    library = get_user_library()

    source_count = library.count(user_id=user.user_id)
    if source_count < 3:
        return {"gaps": [], "total": 0, "detail": "Загрузите больше источников (минимум 3) для анализа пробелов"}

    gaps = paper_store.get_reference_gaps(limit=30)
    return {"gaps": gaps, "total": len(gaps)}


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


def _citekey_from_filename(filename: str) -> str:
    """Generate a citekey from a PDF filename.

    'Smith_2020_Machine_Learning.pdf' → 'smith2020machineLearning'
    """
    name = filename.rsplit(".", 1)[0]  # remove .pdf
    parts = re.split(r"[_\-\s]+", name)
    if not parts:
        return "unknown"
    result = parts[0].lower()
    for p in parts[1:]:
        if p:
            result += p[0].upper() + p[1:].lower() if len(p) > 1 else p.upper()
    return re.sub(r"[^a-zA-Z0-9]", "", result) or "unknown"
