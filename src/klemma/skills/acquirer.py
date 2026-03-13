"""Acquire papers: download PDF → generate citekey → register in klemma."""

import ipaddress
import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

from ..hashing import compute_pdf_hash
from ..literature.metadata import resolve_metadata

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB hard limit
_USER_AGENT = "Mozilla/5.0 (compatible; Klemma/1.0; +https://github.com/klemma-ai/klemma)"


@dataclass
class PaperMetadata:
    """Metadata for a paper to acquire."""

    url: str
    title: str = ""
    authors: str = ""  # comma-separated: "Фамилия И.О., Фамилия И.О."
    year: Optional[int] = None
    journal: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    doi: str = ""
    pdf_override: str = ""  # direct PDF URL (bypass DOI resolution)
    sections: list[str] = field(default_factory=list)


@dataclass
class AcquireResult:
    """Result of acquiring a paper."""

    citekey: str = ""
    pdf_path: str = ""
    status: str = ""  # ok, download_failed
    zotero_added: bool = False
    pdf_hash: str = ""  # SHA256 of PDF bytes (ADR-014: content-addressable dedup)


def _is_allowed_download_url(url: str) -> bool:
    """Allow only safe external HTTP(S) URLs."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False

    host = (parsed.hostname or "").lower()
    if not host or host == "localhost" or host.endswith(".local"):
        return False

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True

    blocked = (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )
    return not blocked


def download_pdf(
    url: str,
    timeout: int = 60,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> Optional[Path]:
    """Download PDF from URL to a temp file. Returns path or None."""
    if not _is_allowed_download_url(url):
        logger.warning("Blocked unsafe URL: %s", url)
        return None

    tmp = None
    try:
        resp = requests.get(
            url, timeout=timeout, stream=True, allow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()

        # Detect WAF/anti-bot challenges (202 with text/html = JS challenge page)
        if resp.status_code == 202:
            ct = resp.headers.get("content-type", "")
            if "html" in ct.lower():
                logger.warning(
                    "Publisher uses anti-bot protection (WAF); "
                    "download manually or use Zotero → Find Available PDF"
                )
                return None

        content_length = resp.headers.get("content-length")
        if content_length and int(content_length) > max_bytes:
            logger.warning(
                "File too large (%s bytes > %d bytes limit)",
                content_length, max_bytes,
            )
            return None

        content_type = resp.headers.get("content-type", "")
        if "pdf" not in content_type.lower() and not url.lower().endswith(".pdf"):
            logger.warning("URL may not be a PDF (content-type: %s)", content_type)

        tmp = tempfile.NamedTemporaryFile(
            prefix="klemma_acquire_", suffix=".pdf", delete=False
        )
        size = 0
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                continue
            tmp.write(chunk)
            size += len(chunk)
            if size > max_bytes:
                logger.warning("Downloaded file exceeds max size limit (%d bytes)", max_bytes)
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
                return None
        tmp.close()

        if size < 10_000:
            logger.warning("Downloaded file too small (%d bytes), may not be a valid PDF", size)
            Path(tmp.name).unlink(missing_ok=True)
            return None

        # Validate PDF magic bytes
        with open(tmp.name, "rb") as f:
            magic = f.read(5)
        if magic != b"%PDF-":
            logger.warning("Downloaded file is not a PDF (magic: %r)", magic[:20])
            Path(tmp.name).unlink(missing_ok=True)
            return None

        logger.info("Downloaded %d bytes → %s", size, tmp.name)
        return Path(tmp.name)

    except Exception as e:
        logger.error("Download failed: %s", e)
        if tmp is not None:
            try:
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
            except Exception:
                pass
        return None


def _slugify(text: str, max_len: int = 60) -> str:
    """Convert text to a safe filename slug."""
    slug = re.sub(r"[^\w\s-]", "", text.strip()).strip()
    slug = re.sub(r"[\s]+", "_", slug)
    return slug[:max_len] if slug else "paper"


def _store_pdf_locally(tmp_path: Path, storage_path: str, key: str, title: str) -> Path:
    """Copy PDF from temp to permanent storage location."""
    dest_dir = Path(storage_path) / key
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_slugify(title)}.pdf"
    dest = dest_dir / filename
    shutil.copy2(tmp_path, dest)
    logger.info("Stored PDF → %s", dest)
    return dest


def _generate_citekey(meta: PaperMetadata) -> str:
    """Generate citekey from metadata: author2024_title_slug."""
    first_author = ""
    if meta.authors:
        # Take first author's last name (before comma or space+initial)
        first_author = re.split(r"[,\s]", meta.authors.strip())[0]
        first_author = re.sub(r"[^\w]", "", first_author)
    if not first_author:
        first_author = "unknown"
    year = str(meta.year) if meta.year else ""
    slug = _slugify(meta.title, max_len=30)
    return f"{first_author}{year}_{slug}"


def _resolve_arxiv_pdf_url(url: str) -> Optional[str]:
    """Convert arXiv abstract URL to PDF URL. Returns PDF URL or None."""
    parsed = urlparse(url)
    if parsed.hostname not in {"arxiv.org", "www.arxiv.org"}:
        return None
    # /abs/2001.01520 or /abs/2001.01520v2 → /pdf/2001.01520v2
    m = re.match(r"/abs/(.+)", parsed.path)
    if m:
        pdf_url = f"https://arxiv.org/pdf/{m.group(1)}"
        logger.info("Resolved arXiv abstract → %s", pdf_url)
        return pdf_url
    return None


def _extract_doi(url: str) -> str:
    """Extract DOI from doi.org / dx.doi.org URLs. Returns DOI or empty string."""
    parsed = urlparse(url)
    if parsed.hostname in {"doi.org", "dx.doi.org"}:
        # DOI is the path without leading slash
        return parsed.path.lstrip("/")
    return ""


def _resolve_doi_to_pdf(doi: str) -> Optional[str]:
    """Try to find a downloadable PDF URL for a DOI.

    Resolution chain:
    1. Unpaywall (open-access database)
    2. Follow DOI redirect → apply publisher URL patterns (.xml→.pdf, etc.)
    """
    # 1. Unpaywall
    try:
        from ..evaluation.resolvers import resolve_unpaywall
        pdf_url = resolve_unpaywall(doi)
        if pdf_url:
            logger.info("Resolved DOI %s → %s (Unpaywall)", doi, pdf_url)
            return pdf_url
    except Exception as e:
        logger.debug("Unpaywall resolution failed: %s", e)

    # 2. Follow DOI redirect → publisher URL patterns
    pdf_url = _resolve_publisher_pdf(doi)
    if pdf_url:
        logger.info("Resolved DOI %s → %s (publisher pattern)", doi, pdf_url)
        return pdf_url

    return None


# Publisher URL patterns: (suffix_to_match, replacement)
# Applied to the final URL after following the DOI redirect.
_PUBLISHER_PATTERNS: list[tuple[str, str]] = [
    # Atypon/Silverchair publishers (AMS, AIP, etc.): /doi/DOI → /doi/pdf/DOI
    ("/doi/10.", "/doi/pdf/10."),
    # AMS journals: .xml → .pdf
    (".xml", ".pdf"),
    # Many publishers: /abstract → /pdf, /full → /pdf
    ("/abstract", "/pdf"),
    ("/full", "/pdf"),
    # Springer/Nature: .html → .pdf
    (".html", ".pdf"),
]


def _resolve_publisher_pdf(doi: str) -> Optional[str]:
    """Follow DOI redirect, then try publisher URL patterns or page scrape.

    Strategy:
    1. Follow DOI redirect to get the landing page URL
    2. Try URL suffix patterns (.xml→.pdf, /abstract→/pdf, etc.)
    3. Scrape landing page for PDF download links
    """
    doi_url = f"https://doi.org/{doi}"
    try:
        # Use GET with stream=True — some publishers don't redirect on HEAD
        resp = requests.get(
            doi_url, allow_redirects=True, timeout=15, stream=True,
            headers={"User-Agent": _USER_AGENT},
        )
        final_url = resp.url
        # Read content only if we need it for scraping (step 3)
        page_content = None
    except Exception as e:
        logger.debug("DOI redirect failed: %s", e)
        return None

    # Step 2: Try URL suffix patterns
    # Trust pattern-derived .pdf URLs without verification — many publishers
    # (AMS, Springer) return 202/403 for programmatic HEAD/GET but serve
    # the PDF fine in practice (anti-bot measures).
    for suffix, replacement in _PUBLISHER_PATTERNS:
        if suffix in final_url:
            candidate = final_url.replace(suffix, replacement, 1)
            logger.info("Publisher pattern %s→%s: %s", suffix, replacement, candidate)
            resp.close()
            return candidate

    # Step 3: Scrape landing page for PDF links
    try:
        page_content = resp.text
        resp.close()
    except Exception:
        resp.close()
        return None

    pdf_url = _scrape_pdf_link(page_content, final_url)
    if pdf_url and _check_pdf_url(pdf_url):
        return pdf_url

    return None


def _scrape_pdf_link(html: str, page_url: str) -> Optional[str]:
    """Extract the most likely full-text PDF link from a publisher landing page."""
    import re as _re
    from urllib.parse import urljoin

    # Find all href="...*.pdf..." links
    pdf_hrefs = _re.findall(r'href="([^"]*\.pdf[^"]*)"', html)
    if not pdf_hrefs:
        return None

    # Prefer links that look like the main article PDF (not supplements/tables)
    # Filter out supplement/table PDFs (common suffixes: -t01, -supplement, -si)
    main_pdfs = [
        h for h in pdf_hrefs
        if not _re.search(r'-(t\d+|supplement|si|supp)\b', h, _re.IGNORECASE)
    ]
    candidates = main_pdfs or pdf_hrefs

    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for href in candidates:
        full = urljoin(page_url, href)
        if full not in seen:
            seen.add(full)
            unique.append(full)

    return unique[0] if unique else None


def _check_pdf_url(url: str) -> bool:
    """Verify a URL points to a PDF via GET with streaming (more reliable than HEAD)."""
    try:
        resp = requests.get(
            url, allow_redirects=True, timeout=10, stream=True,
            headers={"User-Agent": _USER_AGENT},
        )
        if resp.status_code not in (200, 206):
            resp.close()
            return False
        ct = resp.headers.get("content-type", "")
        if "pdf" in ct.lower():
            resp.close()
            return True
        # Some servers lie about content-type — check magic bytes
        chunk = resp.raw.read(5)
        resp.close()
        return chunk == b"%PDF-"
    except Exception:
        return False


def _try_zotero(
    meta: PaperMetadata,
    resolved: dict,
    pdf_path: Optional[Path],
) -> Optional[str]:
    """Try to add paper to Zotero, return BBT citekey or None."""
    try:
        from ..literature.zotero_api import create_zotero_item, get_bbt_citekey, is_zotero_running
        if is_zotero_running():
            ok = create_zotero_item(
                meta.title, meta.authors, meta.year, meta.doi,
                resolved.get("abstract", ""), pdf_path,
            )
            if ok:
                return get_bbt_citekey(meta.title)
    except Exception as e:
        logger.warning("Zotero integration failed: %s", e)
    return None


def _enrich_metadata(meta: PaperMetadata) -> dict:
    """Fill missing metadata from CrossRef (by DOI) or S2 (by title).

    CrossRef is tried first when we have a DOI (common for DOI-only acquires).
    S2 is tried when we have a title but no DOI.
    Mutates meta in-place, returns resolved dict for abstract etc.
    """
    resolved: dict = {}

    # 1. CrossRef by DOI — best for DOI-only acquires (no title needed)
    if meta.doi and (not meta.title or not meta.authors):
        try:
            cr = _lookup_crossref_by_doi(meta.doi)
            if cr:
                resolved = cr
                if not meta.title and cr.get("title"):
                    meta.title = cr["title"]
                if not meta.authors and cr.get("authors"):
                    meta.authors = cr["authors"]
                if meta.year is None and cr.get("year"):
                    meta.year = cr["year"]
        except Exception as e:
            logger.debug("CrossRef DOI lookup failed: %s", e)

    # 2. S2 by title — fills abstract (CrossRef doesn't have it)
    if meta.title:
        try:
            from ..literature.metadata import lookup_s2
            hit = lookup_s2(meta.title)
            if hit:
                if not resolved:
                    resolved = hit
                if not meta.title and hit.get("title"):
                    meta.title = hit["title"]
                if not meta.authors and hit.get("authors"):
                    meta.authors = hit["authors"]
                if meta.year is None and hit.get("year"):
                    meta.year = hit["year"]
                if not meta.doi and hit.get("doi"):
                    meta.doi = hit["doi"]
                # S2 has abstracts, CrossRef usually doesn't
                if hit.get("abstract") and not resolved.get("abstract"):
                    resolved["abstract"] = hit["abstract"]
        except Exception as e:
            logger.debug("S2 metadata lookup failed: %s", e)

    return resolved


def _lookup_crossref_by_doi(doi: str) -> Optional[dict]:
    """Look up paper metadata on CrossRef by DOI (direct, no search needed)."""
    url = f"https://api.crossref.org/works/{doi}"
    try:
        resp = requests.get(
            url, timeout=10,
            headers={"User-Agent": "klemma/0.4 (mailto:klemma@example.com)"},
        )
        resp.raise_for_status()
        item = resp.json().get("message", {})
    except Exception as e:
        logger.debug("CrossRef works/%s failed: %s", doi, e)
        return None

    title = " ".join(item.get("title", []))
    if not title:
        return None

    authors = ", ".join(
        f"{a.get('family', '')} {a.get('given', '')}".strip()
        for a in item.get("author", [])
    )

    year = None
    for date_field in ("published-print", "issued", "published-online"):
        parts = item.get(date_field, {}).get("date-parts", [[]])
        if parts and parts[0] and parts[0][0]:
            year = int(parts[0][0])
            break

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "abstract": "",  # CrossRef rarely has abstracts
    }


def _acquire_metadata_only(
    meta: PaperMetadata,
    storage_path: str,
    state=None,
) -> AcquireResult:
    """Acquire paper without a PDF — DOI-only registration.

    Creates Zotero item (which can later "Find Available PDF"),
    registers in klemma DB with metadata from S2/CrossRef.
    """
    logger.info("Metadata-only acquire for DOI %s (no PDF available)", meta.doi)

    # Enrich metadata from CrossRef (by DOI) / S2 (by title)
    resolved = _enrich_metadata(meta)

    # Add to Zotero (without PDF — Zotero can find it via "Find Available PDF")
    zotero_citekey = _try_zotero(meta, resolved, pdf_path=None)

    # Generate citekey
    citekey = zotero_citekey or _generate_citekey(meta)

    # Register in klemma DB
    if state:
        state.register_sources([citekey])
        if meta.sections:
            chapters = list({int(s.split(".")[0]) for s in meta.sections if "." in s})
            state.set_source_sections(citekey, meta.sections, chapters)
        state.update_source_info(
            citekey,
            title=meta.title or resolved.get("title", ""),
            authors=meta.authors or resolved.get("authors", ""),
            year=meta.year or resolved.get("year"),
            abstract=resolved.get("abstract", ""),
            doi=meta.doi or resolved.get("doi", ""),
        )

    return AcquireResult(
        citekey=citekey,
        pdf_path="",
        status="ok_no_pdf",
        zotero_added=zotero_citekey is not None,
    )


def acquire_paper_local(
    meta: PaperMetadata,
    storage_path: str,
    state=None,
    paper_store=None,
    user_library=None,
) -> AcquireResult:
    """Local acquire: download PDF → auto-extract metadata → generate citekey → store → register in DB."""
    # 0a. Extract DOI from URL before any URL rewriting
    doi_from_url = _extract_doi(meta.url)
    if doi_from_url and not meta.doi:
        meta.doi = doi_from_url

    # 0b. DOI dedup check — skip download if library already has this paper
    if meta.doi and paper_store:
        _lib_paper = paper_store.find_paper(doi=meta.doi)
        if _lib_paper:
            logger.info(
                "Library hit (DOI %s, paper_id=%s...)", meta.doi, _lib_paper.paper_id[:8]
            )
            if not meta.title and _lib_paper.title:
                meta.title = _lib_paper.title
            if not meta.authors and _lib_paper.authors:
                meta.authors = _lib_paper.authors
            if meta.year is None and _lib_paper.year:
                meta.year = _lib_paper.year
            citekey = _generate_citekey(meta)
            _doi_resolved = {
                "title": _lib_paper.title,
                "authors": _lib_paper.authors,
                "year": _lib_paper.year,
                "doi": meta.doi,
                "abstract": _lib_paper.abstract,
            }
            zotero_citekey = _try_zotero(meta, _doi_resolved, pdf_path=None)
            if zotero_citekey:
                citekey = zotero_citekey
            if state:
                state.register_sources([citekey])
                if meta.sections:
                    chapters = list({int(s.split(".")[0]) for s in meta.sections if "." in s})
                    state.set_source_sections(citekey, meta.sections, chapters)
                state.update_source_info(
                    citekey,
                    title=_lib_paper.title,
                    authors=_lib_paper.authors,
                    year=_lib_paper.year,
                    abstract=_lib_paper.abstract,
                    doi=meta.doi,
                )
            if user_library:
                try:
                    user_library.add_source(
                        _lib_paper.paper_id, citekey,
                        status="completed" if _lib_paper.pdf_hash else "pending",
                        pdf_path="",
                    )
                except Exception:
                    pass
            return AcquireResult(
                citekey=citekey,
                pdf_path="",
                status="ok_library_doi",
                zotero_added=zotero_citekey is not None,
                pdf_hash=_lib_paper.pdf_hash,
            )

    # 0c. Resolve effective download URL
    # Priority: --pdf override > arXiv resolve > DOI resolve > original URL
    arxiv_pdf = _resolve_arxiv_pdf_url(meta.url)
    if meta.pdf_override:
        meta.url = meta.pdf_override
    elif arxiv_pdf:
        meta.url = arxiv_pdf
    elif doi_from_url:
        pdf_url = _resolve_doi_to_pdf(doi_from_url)
        if pdf_url:
            meta.url = pdf_url
        else:
            logger.warning("Could not resolve DOI %s to a PDF URL", doi_from_url)

    # 0d. Handle local file:// URLs directly
    parsed_url = urlparse(meta.url)
    if parsed_url.scheme == "file":
        from urllib.parse import unquote
        local_file = Path(unquote(parsed_url.path))
        if not local_file.is_file():
            logger.error("Local file not found: %s", local_file)
            return AcquireResult(status="download_failed")
        pdf_path = local_file
    else:
        # 1. Download PDF
        pdf_path = download_pdf(meta.url)
        if not pdf_path:
            # No PDF available — but if we have a DOI, do metadata-only acquire
            if meta.doi:
                return _acquire_metadata_only(meta, storage_path, state)
            return AcquireResult(status="download_failed")

    is_local_file = parsed_url.scheme == "file"

    # 1.5. Compute PDF hash early for content-addressable dedup (ADR-014)
    _pdf_hash = ""
    try:
        _pdf_hash = compute_pdf_hash(pdf_path)
        logger.info("PDF hash: %s...", _pdf_hash[:12])
    except Exception as e:
        logger.debug("Could not compute PDF hash: %s", e)

    # 1.6. Hash dedup check — library already has this exact PDF
    _lib_resolved = None
    if _pdf_hash and paper_store:
        _hash_paper = paper_store.find_paper(pdf_hash=_pdf_hash)
        if _hash_paper:
            logger.info(
                "Library hash hit: %s... (paper_id=%s...)",
                _pdf_hash[:8], _hash_paper.paper_id[:8],
            )
            if not meta.title and _hash_paper.title:
                meta.title = _hash_paper.title
            if not meta.authors and _hash_paper.authors:
                meta.authors = _hash_paper.authors
            if meta.year is None and _hash_paper.year:
                meta.year = _hash_paper.year
            if not meta.doi and _hash_paper.doi:
                meta.doi = _hash_paper.doi
            _lib_resolved = {
                "title": _hash_paper.title,
                "authors": _hash_paper.authors,
                "year": _hash_paper.year,
                "doi": _hash_paper.doi,
                "abstract": _hash_paper.abstract,
            }

    # 2. Auto-extract metadata (CLI flags win → PDF → S2 → empty)
    if _lib_resolved is None:
        try:
            resolved = resolve_metadata(
                pdf_path,
                cli_title=meta.title,
                cli_authors=meta.authors,
                cli_year=meta.year,
                cli_doi=meta.doi,
            )
            # Fill in blanks on meta from resolved data
            if not meta.title and resolved.get("title"):
                meta.title = resolved["title"]
            if not meta.authors and resolved.get("authors"):
                meta.authors = resolved["authors"]
            if meta.year is None and resolved.get("year"):
                meta.year = resolved["year"]
            if not meta.doi and resolved.get("doi"):
                meta.doi = resolved["doi"]
        except Exception as e:
            logger.warning("Metadata extraction failed, continuing: %s", e)
            resolved = {}

        # 2a. CrossRef fallback — when S2 is unavailable (429) and key fields missing
        if meta.doi and (not meta.authors or meta.year is None):
            try:
                cr = _lookup_crossref_by_doi(meta.doi)
                if cr:
                    # CrossRef title is authoritative — prefer over PDF-extracted
                    if cr.get("title"):
                        meta.title = cr["title"]
                        resolved["title"] = cr["title"]
                    if not meta.authors and cr.get("authors"):
                        meta.authors = cr["authors"]
                    if meta.year is None and cr.get("year"):
                        meta.year = cr["year"]
                    if not resolved.get("authors") and cr.get("authors"):
                        resolved["authors"] = cr["authors"]
                    if not resolved.get("year") and cr.get("year"):
                        resolved["year"] = cr["year"]
            except Exception as e:
                logger.debug("CrossRef fallback failed: %s", e)
    else:
        resolved = _lib_resolved

    # 2b. Add to Zotero if running
    zotero_citekey = _try_zotero(meta, resolved, pdf_path)

    # 3. Generate citekey — prefer BBT, fallback to local
    citekey = zotero_citekey or _generate_citekey(meta)

    # 4. Store PDF in local storage
    permanent_path = ""
    if storage_path:
        try:
            dest = _store_pdf_locally(pdf_path, storage_path, citekey, meta.title)
            permanent_path = str(dest)
        except Exception as e:
            logger.error("Local PDF storage failed: %s", e)

    # 5. Register in klemma DB + persist metadata
    if state:
        state.register_sources([citekey])
        if permanent_path:
            state.set_pdf_path(citekey, permanent_path)
        if meta.sections:
            chapters = list({int(s.split(".")[0]) for s in meta.sections if "." in s})
            state.set_source_sections(citekey, meta.sections, chapters)
        # Persist extracted metadata
        state.update_source_info(
            citekey,
            title=resolved.get("title", ""),
            authors=resolved.get("authors", ""),
            year=resolved.get("year"),
            abstract=resolved.get("abstract", ""),
            doi=resolved.get("doi", ""),
        )

    # 6. Write-through to library (ADR-014): register new paper for cross-project dedup
    if paper_store and _pdf_hash and _lib_resolved is None:
        try:
            paper_id = paper_store.register_paper(
                title=resolved.get("title", meta.title),
                pdf_hash=_pdf_hash,
                doi=meta.doi or resolved.get("doi", ""),
                authors=resolved.get("authors", meta.authors),
                year=meta.year or resolved.get("year"),
                abstract=resolved.get("abstract", ""),
            )
            if user_library and paper_id:
                try:
                    user_library.add_source(
                        paper_id, citekey,
                        status="completed",
                        pdf_path=permanent_path or "",
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Library write-through failed: %s", e)

    if not is_local_file:
        pdf_path.unlink(missing_ok=True)
    return AcquireResult(
        citekey=citekey,
        pdf_path=permanent_path or str(pdf_path),
        status="ok",
        zotero_added=zotero_citekey is not None,
        pdf_hash=_pdf_hash,
    )


def load_batch(path: str) -> list[PaperMetadata]:
    """Load papers from a JSON batch file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    papers = []
    for item in data:
        papers.append(PaperMetadata(
            url=item.get("url", ""),
            title=item.get("title", ""),
            authors=item.get("authors", ""),
            year=item.get("year"),
            journal=item.get("journal", ""),
            volume=str(item.get("volume", "")),
            issue=str(item.get("issue", "")),
            pages=item.get("pages", ""),
            doi=item.get("doi", ""),
            sections=item.get("sections", []),
        ))
    return papers


@dataclass
class OnlineSourceRecord:
    """Parsed BibTeX @online entry for direct DB registration."""

    citekey: str
    title: str
    authors: str
    year: Optional[int]
    url: str
    abstract: str = ""


def parse_bibtex_online(bibtex_text: str) -> list[OnlineSourceRecord]:
    """Parse @online{} BibTeX entries from a string.

    Supports single or multiple entries. Only @online type is accepted —
    @article, @book, etc. are ignored (those go through acquire).

    Field extraction handles:
    - Braced values: title = {Some Title}
    - Quoted values: title = "Some Title"
    - Multiline values (braces must be balanced within the block)

    Returns list of OnlineSourceRecord (empty list if none found).
    """
    records = []

    # Find all @online{...} blocks
    entry_pattern = re.compile(
        r"@online\s*\{([^,]+),([^@]*)\}",
        re.IGNORECASE | re.DOTALL,
    )

    for match in entry_pattern.finditer(bibtex_text):
        citekey = match.group(1).strip()
        fields_text = match.group(2)

        def _extract_field(name: str, text: str) -> str:
            """Extract a BibTeX field value (braced or quoted)."""
            # Braced: field = {value}
            m = re.search(
                rf"(?i)\b{name}\s*=\s*\{{((?:[^{{}}]|\{{[^{{}}]*\}})*)\}}",
                text,
            )
            if m:
                return m.group(1).strip()
            # Quoted: field = "value"
            m = re.search(rf'(?i)\b{name}\s*=\s*"([^"]*)"', text)
            if m:
                return m.group(1).strip()
            return ""

        title = _extract_field("title", fields_text)
        url = _extract_field("url", fields_text)
        abstract = _extract_field("abstract", fields_text)

        # Authors: try "author" field first
        raw_authors = _extract_field("author", fields_text)
        # Normalise "Last, First and Last2, First2" → "Last First, Last2 First2"
        if " and " in raw_authors:
            parts = [a.strip() for a in raw_authors.split(" and ")]
            normalised = []
            for part in parts:
                if "," in part:
                    last, first = part.split(",", 1)
                    normalised.append(f"{first.strip()} {last.strip()}")
                else:
                    normalised.append(part)
            authors = ", ".join(normalised)
        else:
            authors = raw_authors

        # Year
        year_str = _extract_field("year", fields_text)
        year: Optional[int] = None
        if year_str and year_str.isdigit():
            year = int(year_str)

        if not citekey:
            continue

        records.append(OnlineSourceRecord(
            citekey=citekey,
            title=title,
            authors=authors,
            year=year,
            url=url,
            abstract=abstract,
        ))

    return records
