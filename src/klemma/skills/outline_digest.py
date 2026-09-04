"""Condensed outline of the dissertation structure for the extraction prompt (plan C3).

The dissertation keeps its working structure in a Markdown file
(`Структура_диссертации_v2.md`): chapters as ``## N. Глава K. Title``,
sections as ``### X.Y. Title`` and numbered items as bullets
``- X.Y.Z. Title: prose… Источники: @a. Ч.`` (nested ``X.Y.Z.W`` bullets
indented). The extractor needs the *index* of that structure — every id with a
short title — so that ``section`` in a fragment can point at the most specific
item, not the prose, citekeys or status marks.

Parsing is deliberately strict about what it reports: every numbered line that
did not parse lands in ``ParsedOutline.unparsed`` so the acceptance test can
demand zero silent losses, and headings that are not chapters (introduction,
conclusion, appendices) are kept as labelled, unnumbered entries.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CHAPTER_RE = re.compile(r"^##\s+\d+\.\s+Глава\s+(\d+)\.\s*(.+?)\s*$")
_H2_RE = re.compile(r"^##\s+(?:\d+\.\s+)?(.+?)\s*$")
_SECTION_RE = re.compile(r"^###\s+(\d+(?:\.\d+)+)\.?\s+(.+?)\s*$")
_ITEM_RE = re.compile(r"^(\s*)-\s+(\d+(?:\.\d+){2,})\.\s+(.+?)\s*$")
_NUMBERED_LINE_RE = re.compile(r"^\s*(?:[-#]+\s+)?\d+(?:\.\d+){1,}\.?\s")
_STATUS_TAIL_RE = re.compile(r"\s*(?:\((?:рисунок|таблица)\))?\s*[ГЧНЭ](?:/[ГЧНЭ])*\.?\s*$")
_TITLE_TERMINATORS = (":", ";", " Источники", " Материал", " — ", " (см.")

_SPECIAL_KEYWORDS = ("введение", "заключение", "приложени", "список литературы", "перечень")


@dataclass
class OutlineItem:
    id: str
    title: str
    level: int  # 2 chapter, 3 section, 4 item, 5 nested item
    chapter: Optional[int] = None


@dataclass
class ParsedOutline:
    items: list[OutlineItem] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)  # labelled non-chapter headings
    unparsed: list[tuple[int, str]] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [i.id for i in self.items if i.level >= 3]

    @property
    def numbered_item_ids(self) -> list[str]:
        return [i.id for i in self.items if i.level >= 4]


def _clean_title(raw: str, max_len: int = 80) -> str:
    text = raw.strip()
    cut = len(text)
    for term in _TITLE_TERMINATORS:
        pos = text.find(term)
        if 0 < pos < cut:
            cut = pos
    text = text[:cut].strip()
    text = _STATUS_TAIL_RE.sub("", text).strip()
    text = text.rstrip(".").strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def parse_structure_file(text: str) -> ParsedOutline:
    """Parse the structure Markdown into ids + titles; report unparsed numbered lines."""
    out = ParsedOutline()
    current_chapter: Optional[int] = None
    in_chapter = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = _CHAPTER_RE.match(line)
        if m:
            current_chapter = int(m.group(1))
            in_chapter = True
            out.items.append(OutlineItem(id=f"Глава {current_chapter}", title=_clean_title(m.group(2), 90),
                                         level=2, chapter=current_chapter))
            continue
        if line.startswith("## "):
            m2 = _H2_RE.match(line)
            label = _clean_title(m2.group(1), 90) if m2 else line[3:].strip()
            in_chapter = False
            current_chapter = None
            if any(k in label.lower() for k in _SPECIAL_KEYWORDS):
                out.sections.append(label)
            continue
        m = _SECTION_RE.match(line)
        if m and in_chapter:
            sec_id = m.group(1)
            out.items.append(OutlineItem(id=sec_id, title=_clean_title(m.group(2)), level=3,
                                         chapter=current_chapter))
            continue
        m = _ITEM_RE.match(line)
        if m and in_chapter:
            indent, item_id, rest = m.groups()
            depth = item_id.count(".")
            level = 5 if depth >= 3 or len(indent) >= 2 else 4
            out.items.append(OutlineItem(id=item_id, title=_clean_title(rest), level=level,
                                         chapter=current_chapter))
            continue
        if in_chapter and _NUMBERED_LINE_RE.match(line) and line.lstrip().startswith(("-", "#")):
            out.unparsed.append((lineno, line.rstrip()))
    return out


def render_outline_digest(
    parsed: ParsedOutline, *, max_chars: int = 12_000, title_max: int = 80,
) -> str:
    """Compact index «id — title» for ALL chapters (never trims other chapters).

    The budget is met only by shortening titles (80 → 60 → 45); the numbered
    ids are never dropped, because they are what the model must reference.
    """
    for cap in (title_max, 60, 45, 30):
        lines: list[str] = []
        for it in parsed.items:
            title = it.title if len(it.title) <= cap else it.title[: cap - 1].rstrip() + "…"
            if it.level == 2:
                lines.append(f"{it.id}. {title}")
            elif it.level == 3:
                lines.append(f"  {it.id} {title}")
            else:
                indent = "    " if it.level == 4 else "      "
                lines.append(f"{indent}{it.id} {title}")
        if parsed.sections:
            lines.append("Прочие разделы: " + "; ".join(parsed.sections))
        digest = "\n".join(lines)
        if len(digest) <= max_chars:
            return digest
    logger.warning("outline digest exceeds %d chars even with 30-char titles", max_chars)
    return digest


def outline_hash(digest: str) -> str:
    return hashlib.sha256(digest.encode("utf-8")).hexdigest()[:16]


def resolve_outline_path(project_root: Optional[Path], outline_file: str) -> Optional[Path]:
    """Relative paths resolve against the project root (directory of KLEMMA.md)."""
    if not outline_file:
        return None
    p = Path(outline_file).expanduser()
    if p.is_absolute():
        return p
    if project_root is None:
        return None
    return (Path(project_root) / p).resolve()


_warned: set[str] = set()


def load_outline_digest(project_root: Optional[Path], project) -> str:
    """Digest for the prompt, or "" when no outline_file is configured / found.

    A missing file warns once per process and yields an empty digest — the
    extractor then behaves exactly as before plan C3.
    """
    outline_file = getattr(project, "outline_file", "") if project is not None else ""
    if not outline_file:
        return ""
    path = resolve_outline_path(project_root, outline_file)
    if path is None or not path.exists():
        key = str(path or outline_file)
        if key not in _warned:
            _warned.add(key)
            logger.warning("outline_file not found: %s — extraction runs without the outline digest", key)
        return ""
    parsed = parse_structure_file(path.read_text(encoding="utf-8"))
    if parsed.unparsed:
        logger.warning(
            "outline %s: %d numbered line(s) did not parse (first: line %d)",
            path.name, len(parsed.unparsed), parsed.unparsed[0][0],
        )
    max_chars = int(getattr(project, "outline_max_chars", 12_000) or 12_000)
    return render_outline_digest(parsed, max_chars=max_chars)


_DIGEST_ID_RE = re.compile(r"^\s*(\d+(?:\.\d+)+)\s", re.M)


def digest_ids(digest: str) -> list[str]:
    """Numbered ids present in a rendered digest (sections and items)."""
    return _DIGEST_ID_RE.findall(digest or "")


def not_extracted(digest: str, covered: set[str]) -> list[str]:
    """Outline ids no fragment or note referenced: deterministic, computed after
    all chunks. Named ``not_extracted`` on purpose — at ~90 % recall the absence
    of an extraction does not prove the paper is silent on the item."""
    ids = digest_ids(digest)
    return [i for i in ids if i not in covered]
