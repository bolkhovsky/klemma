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
    sections: list[str] = field(default_factory=list)


@dataclass
class AcquireResult:
    """Result of acquiring a paper."""

    citekey: str = ""
    pdf_path: str = ""
    status: str = ""  # ok, download_failed
    zotero_added: bool = False


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
    """Try to find a downloadable PDF URL for a DOI via Unpaywall."""
    try:
        from ..evaluation.resolvers import resolve_unpaywall
        pdf_url = resolve_unpaywall(doi)
        if pdf_url:
            logger.info("Resolved DOI %s → %s", doi, pdf_url)
            return pdf_url
    except Exception as e:
        logger.debug("DOI resolution failed: %s", e)
    return None


def acquire_paper_local(
    meta: PaperMetadata,
    storage_path: str,
    state=None,
) -> AcquireResult:
    """Local acquire: download PDF → auto-extract metadata → generate citekey → store → register in DB."""
    # 0. Handle local file:// URLs directly
    parsed_url = urlparse(meta.url)
    if parsed_url.scheme == "file":
        local_file = Path(parsed_url.path)
        if not local_file.is_file():
            logger.error("Local file not found: %s", local_file)
            return AcquireResult(status="download_failed")
        pdf_path = local_file
    else:
        # 0a. If URL is an arXiv abstract page, resolve to PDF URL
        arxiv_pdf = _resolve_arxiv_pdf_url(meta.url)
        if arxiv_pdf:
            meta.url = arxiv_pdf

        # 0b. If URL is a DOI link, extract DOI and resolve to actual PDF URL
        doi_from_url = _extract_doi(meta.url)
        if doi_from_url:
            if not meta.doi:
                meta.doi = doi_from_url
            pdf_url = _resolve_doi_to_pdf(doi_from_url)
            if pdf_url:
                meta.url = pdf_url
            else:
                logger.warning("Could not resolve DOI %s to a PDF URL", doi_from_url)

        # 1. Download PDF
        pdf_path = download_pdf(meta.url)
        if not pdf_path:
            return AcquireResult(status="download_failed")

    is_local_file = parsed_url.scheme == "file"

    # 2. Auto-extract metadata (CLI flags win → PDF → S2 → empty)
    try:
        from ..literature.metadata import resolve_metadata

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

    # 2a. Add to Zotero if running
    zotero_citekey = None
    try:
        from ..literature.zotero_api import create_zotero_item, get_bbt_citekey, is_zotero_running
        if is_zotero_running():
            ok = create_zotero_item(
                meta.title, meta.authors, meta.year, meta.doi,
                resolved.get("abstract", ""), pdf_path,
            )
            if ok:
                zotero_citekey = get_bbt_citekey(meta.title)
    except Exception as e:
        logger.warning("Zotero integration failed: %s", e)

    # 3. Generate citekey — prefer BBT, fallback to local
    citekey = zotero_citekey or _generate_citekey(meta)

    # 4. Store PDF in local storage (skip if Zotero already has it)
    permanent_path = ""
    if storage_path and not zotero_citekey:
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

    if not is_local_file:
        pdf_path.unlink(missing_ok=True)
    return AcquireResult(
        citekey=citekey,
        pdf_path=permanent_path or str(pdf_path),
        status="ok",
        zotero_added=zotero_citekey is not None,
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
