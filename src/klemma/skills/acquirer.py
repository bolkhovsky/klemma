"""Acquire papers: download PDF → add to Zotero → register in klemma."""

import json
import logging
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BBT_POLL_INTERVAL = 2  # seconds
BBT_POLL_TIMEOUT = 30  # seconds


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
    item_key: str = ""
    pdf_path: str = ""
    status: str = ""  # ok, download_failed, zotero_failed, no_citekey


def parse_authors(authors_str: str) -> list[dict]:
    """Parse 'Фамилия И.О., Фамилия И.О.' into Zotero creators format."""
    creators = []
    if not authors_str:
        return creators
    for part in re.split(r",\s*(?=[A-ZА-ЯЁ])", authors_str.strip()):
        part = part.strip()
        if not part:
            continue
        # Try "LastName F.M." pattern
        m = re.match(r"^(.+?)\s+([A-ZА-ЯЁ]\..*?)$", part)
        if m:
            creators.append({
                "creatorType": "author",
                "lastName": m.group(1).strip(),
                "firstName": m.group(2).strip(),
            })
        else:
            # Single name or unparseable
            creators.append({
                "creatorType": "author",
                "lastName": part,
                "firstName": "",
            })
    return creators


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


def poll_bbt_citekey(
    library_json_path: str, item_key: str, timeout: int = BBT_POLL_TIMEOUT
) -> Optional[str]:
    """Poll BBT JSON export for a new citekey matching the Zotero item key."""
    deadline = time.monotonic() + timeout
    json_path = Path(library_json_path)

    if not json_path.exists():
        logger.warning("BBT JSON not found: %s", json_path)
        return None

    while time.monotonic() < deadline:
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            for item in data.get("items", []):
                if item.get("itemKey") == item_key:
                    return item.get("citationKey", item.get("citekey"))
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("BBT poll error: %s", e)
        time.sleep(BBT_POLL_INTERVAL)

    return None


def _slugify(text: str, max_len: int = 60) -> str:
    """Convert text to a safe filename slug."""
    slug = re.sub(r"[^\w\s-]", "", text.strip()).strip()
    slug = re.sub(r"[\s]+", "_", slug)
    return slug[:max_len] if slug else "paper"


def _store_pdf_locally(tmp_path: Path, storage_path: str, item_key: str, title: str) -> Path:
    """Copy PDF from temp to permanent Zotero storage location."""
    dest_dir = Path(storage_path) / item_key
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_slugify(title)}.pdf"
    dest = dest_dir / filename
    shutil.copy2(tmp_path, dest)
    logger.info("Stored PDF → %s", dest)
    return dest


def acquire_paper(
    meta: PaperMetadata,
    zotero_lib,  # ZoteroLibrary instance
    library_json_path: str,
    storage_path: str = "",
    state=None,
) -> AcquireResult:
    """Full acquire pipeline: download → Zotero → citekey → register."""

    # 1. Download PDF
    pdf_path = download_pdf(meta.url)
    if not pdf_path:
        return AcquireResult(status="download_failed")

    # 2. Create Zotero item
    creators = parse_authors(meta.authors)
    date_str = str(meta.year) if meta.year else ""
    kwargs = {}
    if meta.journal:
        kwargs["publicationTitle"] = meta.journal
    if meta.volume:
        kwargs["volume"] = meta.volume
    if meta.issue:
        kwargs["issue"] = meta.issue
    if meta.pages:
        kwargs["pages"] = meta.pages
    if meta.doi:
        kwargs["DOI"] = meta.doi
    if meta.url:
        kwargs["url"] = meta.url

    try:
        item_key = zotero_lib.create_item(
            title=meta.title,
            creators=creators,
            date=date_str,
            **kwargs,
        )
    except Exception as e:
        logger.error("Zotero create failed: %s", e)
        pdf_path.unlink(missing_ok=True)
        return AcquireResult(status=f"zotero_failed: {e}")

    if not item_key:
        pdf_path.unlink(missing_ok=True)
        return AcquireResult(status="zotero_failed: no item key returned")

    # 3. Create attachment record (metadata only) + store PDF locally
    #    (cloud upload via attachment_simple creates broken records when
    #    storage quota is exceeded — create record, place file in local storage)
    permanent_path = ""
    filename = f"{_slugify(meta.title)}.pdf"
    try:
        attachment_key = zotero_lib.create_attachment_record(item_key, filename)
        if attachment_key and storage_path:
            dest = _store_pdf_locally(pdf_path, storage_path, attachment_key, meta.title)
            permanent_path = str(dest)
            logger.info("Attachment %s → local storage", attachment_key)
    except Exception as e:
        logger.warning("Attachment record failed: %s", e)
        # Fallback: store under parent item key
        if storage_path:
            try:
                dest = _store_pdf_locally(pdf_path, storage_path, item_key, meta.title)
                permanent_path = str(dest)
            except Exception as e2:
                logger.error("Local PDF storage failed: %s", e2)

    # 5. Poll for BBT citekey
    citekey = poll_bbt_citekey(library_json_path, item_key)
    if not citekey:
        logger.warning("BBT citekey not found for %s, using item key as fallback", item_key)
        citekey = item_key

    # 6. Register in klemma DB + set pdf_path
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
        item_key=item_key,
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
