"""Zotero local API integration via Connector + Better BibTeX JSON-RPC."""

import logging
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

ZOTERO_BASE = "http://localhost:23119"
BBT_RPC = f"{ZOTERO_BASE}/better-bibtex/json-rpc"
CONNECTOR_SAVE = f"{ZOTERO_BASE}/connector/saveItems"


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


def create_zotero_item(
    title: str,
    authors_str: str,
    year: Optional[int],
    doi: str,
    abstract: str,
    pdf_path: Optional[Path],
) -> bool:
    """Create a Zotero item via the Connector saveItems endpoint."""
    item: dict = {
        "itemType": "journalArticle",
        "title": title,
        "creators": _parse_authors(authors_str),
        "date": str(year) if year else "",
        "DOI": doi,
        "abstractNote": abstract,
    }

    attachments = []
    if pdf_path and Path(pdf_path).is_file():
        attachments.append({
            "title": "Full Text PDF",
            "mimeType": "application/pdf",
            "path": str(Path(pdf_path).resolve()),
        })

    payload = {
        "items": [item],
        "uri": "https://klemma.ai/acquire",
    }
    if attachments:
        payload["items"][0]["attachments"] = attachments

    try:
        resp = requests.post(CONNECTOR_SAVE, json=payload, timeout=10)
        if resp.status_code == 201:
            logger.info("Created Zotero item: %s", title)
            return True
        logger.warning("Zotero saveItems returned %d: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        logger.warning("Zotero saveItems failed: %s", e)
        return False


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
