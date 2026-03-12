"""Fetch and strip HTML from web pages for online source processing."""

from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (compatible; Klemma/1.0; +https://github.com/klemma-ai/klemma)"
_FETCH_TIMEOUT = 15  # seconds
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB response cap


class _TextExtractor(HTMLParser):
    """Minimal HTML parser that extracts visible text, skipping script/style."""

    _SKIP_TAGS = {"script", "style", "noscript", "head", "meta", "link"}
    _BLOCK_TAGS = {
        "p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3",
        "h4", "h5", "h6", "blockquote", "article", "section", "header",
        "footer", "nav", "aside", "figure", "figcaption",
    }

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        raw = html.unescape(raw)
        # Collapse whitespace, preserve paragraph breaks
        lines = [line.strip() for line in raw.splitlines()]
        # Remove blank line runs > 2
        collapsed: list[str] = []
        blank_run = 0
        for line in lines:
            if not line:
                blank_run += 1
                if blank_run <= 2:
                    collapsed.append("")
            else:
                blank_run = 0
                collapsed.append(line)
        return "\n".join(collapsed).strip()


def _strip_html(html_text: str) -> str:
    """Strip HTML tags and return plain text."""
    parser = _TextExtractor()
    try:
        parser.feed(html_text)
        return parser.get_text()
    except Exception:
        # Fallback: regex tag stripping
        text = re.sub(r"<[^>]+>", " ", html_text)
        return html.unescape(text).strip()


def fetch_url_text(url: str, max_chars: int = 200_000) -> str:
    """Fetch a URL and return stripped plain text.

    Returns empty string on network/HTTP errors. Caps output at max_chars.
    Handles gzip-encoded responses transparently (requests does this).

    Limitation: JavaScript-rendered pages (SPAs) are not supported — only
    server-rendered HTML is extracted. Use --no-process for JS-heavy sites
    and process the content manually.
    """
    import requests

    try:
        resp = requests.get(
            url,
            timeout=_FETCH_TIMEOUT,
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            stream=True,
        )
        resp.raise_for_status()

        # Content-type check — only process text/html and text/plain
        ct = resp.headers.get("content-type", "").lower()
        if "html" not in ct and "text" not in ct:
            logger.warning("fetch_url_text: unexpected content-type '%s' for %s", ct, url[:60])
            return ""

        # Stream with byte cap
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            chunks.append(chunk)
            total += len(chunk)
            if total >= _MAX_BYTES:
                logger.debug("fetch_url_text: capped at %d bytes for %s", _MAX_BYTES, url[:60])
                break

        raw = b"".join(chunks).decode("utf-8", errors="replace")

        if "html" in ct:
            text = _strip_html(raw)
        else:
            text = raw

        if len(text) > max_chars:
            text = text[:max_chars]

        logger.debug("fetch_url_text: %d chars from %s", len(text), url[:60])
        return text

    except Exception as exc:
        logger.warning("fetch_url_text failed for '%s': %s", url[:60], exc)
        return ""
