"""Parse structure and bibliography from draft PDFs/DOCX (#76).

Extracts section headings, body text, and bibliography entries from
academic drafts. Used by the Klemma onboarding `--from-draft` pipeline
to bootstrap a project from an existing paper.

Uses PyMuPDF (already a core dependency) for PDF parsing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from .reference_parser import ParsedReference, parse_references


@dataclass
class DetectedSection:
    """A section detected in the draft."""

    heading: str
    level: int  # 1 = chapter/top-level, 2 = section, 3 = subsection
    text: str = ""
    page_start: int = 0


@dataclass
class DraftParseResult:
    """Result of parsing a draft document."""

    title: str = ""
    sections: list[DetectedSection] = field(default_factory=list)
    references: list[ParsedReference] = field(default_factory=list)
    full_text: str = ""
    page_count: int = 0


# Heading patterns — detect section-like lines by font size or numbering
_NUMBERED_HEADING_RE = re.compile(
    r"^(\d+(?:\.\d+)*)\s*[.\s]+(.+)",
)

# Bibliography section markers
_BIB_MARKERS = [
    "references",
    "bibliography",
    "литература",
    "список литературы",
    "список использованных источников",
    "список использованной литературы",
    "works cited",
    "cited references",
]


def parse_draft_pdf(pdf_path: str | Path) -> DraftParseResult:
    """Parse a PDF draft to extract structure and references.

    Returns DraftParseResult with detected sections, bibliography
    references, and full text. Best-effort — never raises on
    malformed PDFs (returns partial results).
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return DraftParseResult()

    doc = fitz.open(str(pdf_path))
    result = DraftParseResult(page_count=len(doc))

    # Extract full text with page markers
    pages: list[str] = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        pages.append(text)

    result.full_text = "\n".join(pages)

    # Extract title from first page (largest font block)
    if pages:
        result.title = _extract_title(doc[0])

    # Detect sections via numbered headings
    result.sections = _detect_sections(pages)

    # Extract bibliography
    result.references = _extract_bibliography(result.full_text)

    doc.close()
    return result


def _extract_title(page: fitz.Page) -> str:
    """Extract the title from the first page using font size heuristic."""
    blocks = page.get_text("dict")["blocks"]
    best_text = ""
    best_size = 0.0

    for block in blocks:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                size = span["size"]
                text = span["text"].strip()
                # Skip very short spans and page numbers
                if len(text) < 5 or text.isdigit():
                    continue
                if size > best_size:
                    best_size = size
                    best_text = text

    return best_text


def _detect_sections(pages: list[str]) -> list[DetectedSection]:
    """Detect sections from numbered headings in the text."""
    sections: list[DetectedSection] = []
    all_text = "\n".join(pages)
    lines = all_text.split("\n")

    current_section: DetectedSection | None = None

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            if current_section:
                current_section.text += "\n"
            continue

        # Check for numbered heading
        m = _NUMBERED_HEADING_RE.match(line_stripped)
        if m and len(line_stripped) < 200:  # headings are short
            number = m.group(1)
            heading_text = m.group(2).strip()
            level = number.count(".") + 1

            if current_section:
                current_section.text = current_section.text.strip()
                sections.append(current_section)

            current_section = DetectedSection(
                heading=f"{number} {heading_text}",
                level=level,
            )
            continue

        # Check for bibliography marker (stops section detection)
        if _is_bib_marker(line_stripped):
            if current_section:
                current_section.text = current_section.text.strip()
                sections.append(current_section)
                current_section = None
            break

        if current_section:
            current_section.text += line_stripped + "\n"

    if current_section:
        current_section.text = current_section.text.strip()
        sections.append(current_section)

    return sections


def _is_bib_marker(line: str) -> bool:
    """Check if a line is a bibliography section header."""
    normalized = line.lower().strip()
    # Strip markdown heading prefix ("## Список литературы")
    normalized = re.sub(r"^#{1,6}\s*", "", normalized).rstrip(".")
    # Strip numbered prefix
    normalized = re.sub(r"^\d+(?:\.\d+)*\s*[.\s]*", "", normalized)
    return normalized in _BIB_MARKERS


def find_bibliography_section(text: str) -> tuple[int, int] | None:
    """Locate the bibliography content span in text.

    Returns (start, end) character offsets into text: start — first character
    after the bibliography marker line ("References", "Список литературы", …),
    end — start of the next markdown heading after the bibliography (trailing
    sections like "## Сведения об авторах"), or len(text) when there is none.
    None when no marker line is found.
    """
    offset = 0
    bib_start: int | None = None

    for line in text.split("\n"):
        stripped = line.strip()
        if bib_start is None:
            if _is_bib_marker(stripped):
                bib_start = min(offset + len(line) + 1, len(text))
        elif re.match(r"^#{1,6}\s+\S", stripped):
            return (bib_start, offset)
        offset += len(line) + 1

    if bib_start is None:
        return None
    return (bib_start, len(text))


def _extract_bibliography(full_text: str) -> list[ParsedReference]:
    """Extract bibliography section and parse individual references."""
    span = find_bibliography_section(full_text)
    if span is None:
        return []
    return parse_references(full_text[span[0]:span[1]])
