"""Library endpoints: user's paper collection CRUD (ADR-009, #99)."""

from __future__ import annotations

import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel

from klemma.models import UserRecord

from ..auth.deps import get_current_user
from ..deps import get_file_store, get_paper_store, get_user_library

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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/sources", response_model=SourceListResponse)
async def list_sources(
    user: UserRecord = Depends(get_current_user),
) -> SourceListResponse:
    """List all sources in the user's library."""
    library = get_user_library()
    paper_store = get_paper_store()

    all_sources = library.get_all_sources()
    results: list[SourceResponse] = []
    for src in all_sources:
        paper = paper_store.get_paper_by_id(src.paper_id)
        results.append(
            SourceResponse(
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
            )
        )

    return SourceListResponse(sources=results, total=len(results))


@router.get("/sources/{citekey}", response_model=SourceDetailResponse)
async def get_source(
    citekey: str,
    user: UserRecord = Depends(get_current_user),
) -> SourceDetailResponse:
    """Get a source with its fragments."""
    library = get_user_library()
    paper_store = get_paper_store()

    src = library.get_source_by_citekey(citekey)
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
    """Add a source to the user's library (metadata only, no PDF upload)."""
    library = get_user_library()
    paper_store = get_paper_store()

    # Check if citekey already exists
    existing = library.get_source_by_citekey(body.citekey)
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

    # Register in user's library
    library.add_source(paper_id, body.citekey, status="pending")

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
    """Remove a source from the user's library.

    Does NOT delete the paper from the global corpus (other users may reference it).
    """
    library = get_user_library()

    src = library.get_source_by_citekey(citekey)
    if src is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{citekey}' not found",
        )

    library.remove_source(citekey)


class UploadResponse(BaseModel):
    """Response from PDF upload."""

    citekey: str
    paper_id: str
    pdf_hash: str
    status: str
    deduplicated: bool = False


MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_pdf(
    file: UploadFile,
    user: UserRecord = Depends(get_current_user),
) -> UploadResponse:
    """Upload a PDF and register it in the library.

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
        citekey = _citekey_from_filename(file.filename)
        # Check citekey conflict
        if library.get_source_by_citekey(citekey):
            citekey = f"{citekey}_{pdf_hash[:6]}"
        library.add_source(existing.paper_id, citekey, status="completed")
        return UploadResponse(
            citekey=citekey,
            paper_id=existing.paper_id,
            pdf_hash=pdf_hash,
            status="completed",
            deduplicated=True,
        )

    # New paper: register + store file
    citekey = _citekey_from_filename(file.filename)
    if library.get_source_by_citekey(citekey):
        citekey = f"{citekey}_{pdf_hash[:6]}"

    paper_id = paper_store.register_paper(
        title=file.filename.rsplit(".", 1)[0],
        pdf_hash=pdf_hash,
    )
    # Sanitize filename for storage (FileStore validates, but give a clean 400)
    safe_filename = re.sub(r"[^\w.\-]", "_", file.filename)
    try:
        file_store.save(paper_id, data, safe_filename)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    library.add_source(paper_id, citekey, status="pending")

    return UploadResponse(
        citekey=citekey,
        paper_id=paper_id,
        pdf_hash=pdf_hash,
        status="pending",
    )


def _citekey_from_filename(filename: str) -> str:
    """Generate a citekey from a PDF filename.

    'Smith_2020_Machine_Learning.pdf' → 'smith2020machineLearning'
    """
    name = filename.rsplit(".", 1)[0]  # remove .pdf
    # Split on common separators
    parts = re.split(r"[_\-\s]+", name)
    if not parts:
        return "unknown"
    # lowercase first part, camelCase rest
    result = parts[0].lower()
    for p in parts[1:]:
        if p:
            result += p[0].upper() + p[1:].lower() if len(p) > 1 else p.upper()
    # Remove non-alphanumeric
    return re.sub(r"[^a-zA-Z0-9]", "", result) or "unknown"


