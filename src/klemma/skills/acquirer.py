"""Acquire papers: download PDF → generate citekey → register in klemma."""

import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


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


def download_pdf(url: str, timeout: int = 60) -> Optional[Path]:
    """Download PDF from URL to a temp file. Returns path or None."""
    try:
        resp = requests.get(url, timeout=timeout, stream=True, allow_redirects=True)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "pdf" not in content_type and not url.endswith(".pdf"):
            logger.warning("URL may not be a PDF (content-type: %s)", content_type)

        tmp = tempfile.NamedTemporaryFile(
            prefix="klemma_acquire_", suffix=".pdf", delete=False
        )
        size = 0
        for chunk in resp.iter_content(chunk_size=8192):
            tmp.write(chunk)
            size += len(chunk)
        tmp.close()

        if size < 10_000:
            logger.warning("Downloaded file too small (%d bytes), may not be a valid PDF", size)
            Path(tmp.name).unlink(missing_ok=True)
            return None

        logger.info("Downloaded %d bytes → %s", size, tmp.name)
        return Path(tmp.name)

    except Exception as e:
        logger.error("Download failed: %s", e)
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


def acquire_paper_local(
    meta: PaperMetadata,
    storage_path: str,
    state=None,
) -> AcquireResult:
    """Local acquire: download PDF → generate citekey → store → register in DB."""
    # 1. Download PDF
    pdf_path = download_pdf(meta.url)
    if not pdf_path:
        return AcquireResult(status="download_failed")

    # 2. Generate citekey locally
    citekey = _generate_citekey(meta)

    # 3. Store PDF in local storage
    permanent_path = ""
    if storage_path:
        try:
            dest = _store_pdf_locally(pdf_path, storage_path, citekey, meta.title)
            permanent_path = str(dest)
        except Exception as e:
            logger.error("Local PDF storage failed: %s", e)

    # 4. Register in klemma DB
    if state:
        state.register_sources([citekey])
        if permanent_path:
            state.set_pdf_path(citekey, permanent_path)
        if meta.sections:
            chapters = list({int(s.split(".")[0]) for s in meta.sections if "." in s})
            with state._conn() as conn:
                state._set_sections_inline(conn, citekey, meta.sections, chapters)

    pdf_path.unlink(missing_ok=True)
    return AcquireResult(
        citekey=citekey,
        pdf_path=permanent_path or str(pdf_path),
        status="ok",
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
