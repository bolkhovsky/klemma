"""Raw PDF text sidecar writer.

Produces a human-readable dump of a processed PDF alongside the
structured fragments the AI extractor emits. The dump lands at
``<project_root>/.klemma/pdfs/<citekey>.md`` and is a stable,
regex-greppable format intended for two consumers:

1. Humans debugging why a given fragment was (or wasn't) extracted.
2. Downstream tooling (semantic citation drift checker) that needs
   the primary-source text without re-opening the PDF.

Three format contracts that downstream consumers may rely on:

* The sidecar path is fixed at ``<project_root>/.klemma/pdfs/<citekey>.md``.
* Pages are separated by exactly ``\\n<!-- Page N -->\\n`` (``N >= 2``).
  Page 1 has no marker — it starts immediately after the ``---`` divider.
* Frontmatter lines for ``Citekey``, ``Authors``, ``Year``, ``DOI``,
  ``Pages``, and ``Source`` form the stable set. Additions are allowed;
  renames or removals require a version bump note.

Reading contract: ``read_pdf_sidecar`` returns the *canonical text* —
frontmatter stripped, each page marker replaced by a single ``\\n``, then
``str.strip()``. ``load_sidecar_doc`` returns the same text byte-for-byte
plus per-page character spans derived from the markers at read time (no
extra storage): fragment/claim offsets are always expressed in canonical
text coordinates.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_PAGE_MARKER_RE = re.compile(r"\n<!-- Page (\d+) -->\n")


def _validate_citekey(citekey: str) -> None:
    if not citekey or ".." in citekey or "/" in citekey or "\\" in citekey:
        raise ValueError(f"Invalid citekey: {citekey!r}")


def _format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v is not None and str(v))
    return str(value)


def _render_frontmatter(citekey: str, pages: int, metadata: Mapping[str, Any]) -> list[str]:
    title = _format_value(metadata.get("title") or citekey)
    lines = [
        f"# {title}",
        "",
        f"> Citekey: {citekey}",
        f"> Authors: {_format_value(metadata.get('authors'))}",
        f"> Year: {_format_value(metadata.get('year'))}",
        f"> DOI: {_format_value(metadata.get('doi'))}",
        f"> Pages: {pages}",
        f"> Source: {_format_value(metadata.get('source'))}",
        "",
        "---",
        "",
    ]
    return lines


def _render_body(pages: list[str]) -> str:
    if not pages:
        return ""
    chunks: list[str] = [pages[0].rstrip()]
    for page_num, page_text in enumerate(pages[1:], start=2):
        chunks.append(f"<!-- Page {page_num} -->")
        chunks.append(page_text.rstrip())
    return "\n\n".join(chunks) + "\n"


@dataclass
class SidecarDoc:
    """Canonical sidecar text plus per-page character spans.

    ``text`` is byte-for-byte identical to ``read_pdf_sidecar()`` output.
    ``page_spans`` holds ``(page, char_start, char_end)`` half-open
    intervals into ``text``, trimmed to each page's non-whitespace
    content; whitespace-only pages get no span.
    """

    text: str
    page_spans: list[tuple[int, int, int]]

    def page_for(self, offset: int) -> int | None:
        """Return the page containing ``offset``, or None (gap / out of range)."""
        for page, start, end in self.page_spans:
            if start <= offset < end:
                return page
        return None


def load_sidecar_doc(project_root: Path, citekey: str) -> SidecarDoc | None:
    """Load a PDF sidecar as canonical text with page offsets.

    Applies ``_validate_citekey`` before building the path (anti-traversal).
    Returns ``None`` when the citekey is invalid, the file does not exist,
    or the body is empty after stripping.
    """
    try:
        _validate_citekey(citekey)
    except ValueError:
        return None

    path = Path(project_root) / ".klemma" / "pdfs" / f"{citekey}.md"
    if not path.exists():
        return None

    raw = path.read_text(encoding="utf-8")
    # Strip the frontmatter header — everything up to the first "---" divider line
    parts = raw.split("\n---\n", 1)
    body = parts[1] if len(parts) > 1 else raw

    # Rebuild the canonical text exactly as the historical read path did —
    # each "\n<!-- Page N -->\n" marker becomes a single "\n" — while
    # remembering which page each inter-marker segment belongs to.
    pieces: list[str] = []
    raw_spans: list[tuple[int, int, int]] = []  # (page, start, end) pre-strip
    cursor = 0
    page = 1
    last = 0
    for m in _PAGE_MARKER_RE.finditer(body):
        segment = body[last:m.start()]
        pieces.append(segment)
        raw_spans.append((page, cursor, cursor + len(segment)))
        cursor += len(segment)
        pieces.append("\n")
        cursor += 1
        page = int(m.group(1))
        last = m.end()
    segment = body[last:]
    pieces.append(segment)
    raw_spans.append((page, cursor, cursor + len(segment)))

    text = "".join(pieces)
    lead = len(text) - len(text.lstrip())
    stripped = text.strip()
    if not stripped:
        return None

    # Shift spans into stripped coordinates and trim each to the page's
    # non-whitespace content; whitespace-only pages are dropped.
    page_spans: list[tuple[int, int, int]] = []
    for pg, start, end in raw_spans:
        segment = text[start:end]
        left = len(segment) - len(segment.lstrip())
        right = len(segment) - len(segment.rstrip())
        s = start + left - lead
        e = end - right - lead
        if s < e:
            page_spans.append((pg, max(s, 0), min(e, len(stripped))))

    return SidecarDoc(text=stripped, page_spans=page_spans)


def read_pdf_sidecar(project_root: Path, citekey: str) -> str | None:
    """Return the prose body of a PDF sidecar, stripped of frontmatter and page markers.

    Delegates to ``load_sidecar_doc`` — the returned string is byte-for-byte
    the canonical text that page spans are expressed in. Returns ``None``
    when the citekey is invalid, the file does not exist, or the body is
    empty after stripping.
    """
    doc = load_sidecar_doc(project_root, citekey)
    return doc.text if doc else None


def write_pdf_sidecar(
    project_root: Path,
    citekey: str,
    pages: list[str],
    metadata: Mapping[str, Any],
) -> Path:
    """Write a raw PDF sidecar for ``citekey`` under ``project_root``.

    The write is atomic: content is staged in a temp file next to the
    final destination and swapped in via ``os.replace``. Reprocessing a
    source overwrites the existing sidecar cleanly.
    """
    _validate_citekey(citekey)

    pdfs_dir = Path(project_root) / ".klemma" / "pdfs"
    pdfs_dir.mkdir(parents=True, exist_ok=True)

    target = pdfs_dir / f"{citekey}.md"

    header = "\n".join(_render_frontmatter(citekey, len(pages), metadata))
    body = _render_body(pages)
    content = header + body

    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{citekey}.", suffix=".md.tmp", dir=str(pdfs_dir)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise

    return target
