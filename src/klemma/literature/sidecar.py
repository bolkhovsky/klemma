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
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping


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


def read_pdf_sidecar(project_root: Path, citekey: str) -> str | None:
    """Return the prose body of a PDF sidecar, stripped of frontmatter and page markers.

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

    text = path.read_text(encoding="utf-8")
    # Strip the frontmatter header — everything up to the first "---" divider line
    parts = text.split("\n---\n", 1)
    body = parts[1] if len(parts) > 1 else text
    # Remove page markers: "\n<!-- Page N -->\n"
    body = re.sub(r"\n<!-- Page \d+ -->\n", "\n", body)
    return body.strip() or None


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
