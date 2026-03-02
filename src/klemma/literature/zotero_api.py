"""Zotero local API integration via Connector + Better BibTeX JSON-RPC."""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

ZOTERO_BASE = "http://localhost:23119"
BBT_RPC = f"{ZOTERO_BASE}/better-bibtex/json-rpc"
CONNECTOR_SAVE = f"{ZOTERO_BASE}/connector/saveItems"
CONNECTOR_ATTACH = f"{ZOTERO_BASE}/connector/saveAttachment"


def is_zotero_running() -> bool:
    """Check if Zotero + BBT are available on localhost."""
    try:
        resp = requests.post(
            BBT_RPC,
            json={"jsonrpc": "2.0", "method": "api.ready", "id": 1},
            timeout=2,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _parse_authors(authors_str: str) -> list[dict]:
    """Parse 'Smith J., Jones K.' into Zotero creators array."""
    creators = []
    if not authors_str:
        return creators
    for part in authors_str.split(","):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        if len(tokens) >= 2:
            last_name = tokens[0]
            first_name = " ".join(tokens[1:])
        else:
            last_name = tokens[0]
            first_name = ""
        creators.append({
            "creatorType": "author",
            "lastName": last_name,
            "firstName": first_name,
        })
    return creators


def _attach_pdf(session_id: str, item_id: str, pdf_path: Path) -> bool:
    """Attach a PDF to a Zotero item via the Connector saveAttachment endpoint."""
    resolved = Path(pdf_path).resolve()
    if not resolved.is_file():
        return False

    pdf_bytes = resolved.read_bytes()
    metadata = json.dumps({
        "sessionID": session_id,
        "parentItemID": item_id,
        "title": "Full Text PDF",
        "url": f"klemma://acquire/{resolved.name}",
    })

    try:
        resp = requests.post(
            CONNECTOR_ATTACH,
            data=pdf_bytes,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": str(len(pdf_bytes)),
                "X-Metadata": metadata,
            },
            timeout=30,
        )
        if resp.status_code == 201:
            logger.info("Attached PDF to Zotero item: %s", resolved.name)
            return True
        logger.warning("Zotero saveAttachment returned %d: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.warning("Zotero saveAttachment failed: %s", e)
        return False


def create_zotero_item(
    title: str,
    authors_str: str,
    year: Optional[int],
    doi: str,
    abstract: str,
    pdf_path: Optional[Path],
) -> bool:
    """Create a Zotero item via the Connector saveItems + saveAttachment endpoints."""
    session_id = str(uuid.uuid4())
    item_id = "klemma_item_0"

    item: dict = {
        "id": item_id,
        "itemType": "journalArticle",
        "title": title,
        "creators": _parse_authors(authors_str),
        "date": str(year) if year else "",
        "DOI": doi,
        "abstractNote": abstract,
    }

    payload = {
        "sessionID": session_id,
        "items": [item],
        "uri": "https://klemma.ai/acquire",
    }

    try:
        resp = requests.post(CONNECTOR_SAVE, json=payload, timeout=10)
        if resp.status_code != 201:
            logger.warning("Zotero saveItems returned %d: %s", resp.status_code, resp.text[:200])
            return False
        logger.info("Created Zotero item: %s", title)
    except Exception as e:
        logger.warning("Zotero saveItems failed: %s", e)
        return False

    # Step 2: Attach PDF via separate endpoint
    if pdf_path and Path(pdf_path).is_file():
        _attach_pdf(session_id, item_id, Path(pdf_path))

    return True


def get_bbt_citekey(title: str, retries: int = 3, delay: float = 1.0) -> Optional[str]:
    """Get the BBT-generated citekey for a paper by title search."""
    for attempt in range(retries):
        try:
            resp = requests.post(
                BBT_RPC,
                json={
                    "jsonrpc": "2.0",
                    "method": "item.search",
                    "params": [title],
                    "id": 1,
                },
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("result", [])
                if results and isinstance(results, list):
                    citekey = results[0].get("citekey")
                    if citekey:
                        logger.info("BBT citekey: %s", citekey)
                        return citekey
        except Exception as e:
            logger.debug("BBT search attempt %d failed: %s", attempt + 1, e)

        if attempt < retries - 1:
            time.sleep(delay)

    logger.warning("Could not get BBT citekey for '%s' after %d attempts", title, retries)
    return None
