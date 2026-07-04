"""Meeting-report domain logic (Bonum B2B portal MVP) — no CLI/API deps.

Parses meeting protocols (markdown, optionally with YAML frontmatter) into
``source_type='meeting'`` sources whose extracted items (summary points,
decisions, tasks) become ``fragments``. No PDF, no LLM re-extraction — the
upstream MyMeet→Nodul pipeline already produced the structured content; this
module only parses, maps, and embeds it for semantic search / RAG.

Layering: this module is imported by both the CLI command
(``commands/meetings.py``) and the SaaS route (``api/routes/meetings.py``) plus
the seed script, so it must NOT import ``klemma.cli`` or any FastAPI symbol.

Storage notes
-------------
* Per-fragment metadata the rigid ``fragments`` schema has no column for
  (speaker, timecode, assignee, deadline, task status) is stored as a JSON blob
  in ``usage_hint``; consumers parse it back. ``fragment_text`` holds the
  embeddable text.
* Meeting-level metadata (date/type/site/duration/speakers/decisions/summary)
  is stored as JSON in a nullable ``meeting_meta`` column added by a *guarded*
  ALTER on the project DB only (``_ensure_meeting_meta_column``) — intentionally
  NOT part of ``StateManager._migrate_schema`` so the global schema chain stays
  untouched.
* Import-time and query-time embeddings come from the SAME provider built by
  ``build_state_and_embeddings`` (project config), so ``embedding_model`` always
  matches and ``retrieve_similar_fragments(model=...)`` finds the vectors.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from .meetings_sites import (
    ensure_portal_tables,
    get_sites,
    resolve_site_slug,
    site_display_names,
)

# ── Block headers in a protocol MD (bold lines) ───────────────────────────────
_BLOCK_SUMMARY = ("супер крат", "краткое содерж", "tl;dr")
_BLOCK_THEMES = ("саммари по темам", "по темам", "обсуждение")
_BLOCK_DECISIONS = ("принятые решен", "решения", "decisions")
_BLOCK_TASKS = ("задачи", "извлечённые задачи", "извлеченные задачи", "tasks")

# Timecode like [0:00], [2:01], [1:02:33] (optionally followed by a (#...) link)
_TIMECODE_RE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")
_ASSIGNEE_RE = re.compile(r"\*\*\s*assignee\s*:?\s*\*\*\s*([^,)\n]+)", re.IGNORECASE)
_DEADLINE_RE = re.compile(r"\*\*\s*deadline\s*:?\s*\*\*\s*([^)\n]+)", re.IGNORECASE)
_OVERDUE_RE = re.compile(r"просроч", re.IGNORECASE)


@dataclass
class ParsedTask:
    action: str
    assignee: str = ""
    deadline: str = ""
    timecode: str = ""
    status: str = "new"
    overdue: bool = False


@dataclass
class ParsedPoint:
    text: str
    theme: str = ""
    speaker: str = ""
    timecode: str = ""


@dataclass
class ParsedMeeting:
    title: str = ""
    summary: str = ""
    themes: list[str] = field(default_factory=list)
    points: list[ParsedPoint] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    tasks: list[ParsedTask] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


# ── Parsing ───────────────────────────────────────────────────────────────────


def _strip_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter (``---`` delimited at file start) from the body."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end].strip("\n")
    body = text[end + 4 :].lstrip("\n")
    try:
        import yaml

        meta = yaml.safe_load(raw) or {}
        if not isinstance(meta, dict):
            meta = {}
        return meta, body
    except Exception:
        return _parse_simple_frontmatter(raw), body


def _parse_simple_frontmatter(raw: str) -> dict:
    """Minimal ``key: value`` / inline-list frontmatter parser (no PyYAML)."""
    meta: dict = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if not key:
            continue
        if val.startswith("[") and val.endswith("]"):
            items = [v.strip().strip("'\"") for v in val[1:-1].split(",")]
            meta[key] = [v for v in items if v]
        else:
            meta[key] = val.strip("'\"")
    return meta


def _timecode(s: str) -> str:
    m = _TIMECODE_RE.search(s)
    return m.group(1) if m else ""


def _clean_bullet(line: str) -> str:
    """Strip a leading list marker and trailing timecode / anchor links."""
    text = line.lstrip()
    text = re.sub(r"^[-*•]\s+", "", text)
    text = _TIMECODE_RE.sub("", text)
    text = re.sub(r"\(#[^)]*\)", "", text)
    return text.strip(" .;")


def _detect_speaker(text: str, speakers: list[str]) -> str:
    """Best-effort: return the meeting speaker whose name leads the bullet."""
    low = text.lower()
    for sp in speakers:
        name = sp.strip()
        if not name:
            continue
        if low.startswith(name.lower()):
            return name
        first = name.split()[0].lower()
        if first and low.startswith(first):
            return name
    return ""


def _block_kind(line: str) -> Optional[str]:
    bare = line.strip().strip("*# ").rstrip(":").strip().lower()
    if not bare:
        return None
    if any(k in bare for k in _BLOCK_SUMMARY):
        return "summary"
    if any(k in bare for k in _BLOCK_THEMES):
        return "themes"
    if any(k in bare for k in _BLOCK_DECISIONS):
        return "decisions"
    if any(k in bare for k in _BLOCK_TASKS):
        return "tasks"
    return None


def _parse_task_bullet(line: str) -> ParsedTask:
    timecode = _timecode(line)
    assignee_m = _ASSIGNEE_RE.search(line)
    deadline_m = _DEADLINE_RE.search(line)
    assignee = assignee_m.group(1).strip() if assignee_m else ""
    deadline = deadline_m.group(1).strip().rstrip(")").strip() if deadline_m else ""
    action = re.split(r"\(\s*\*\*\s*assignee", line, flags=re.IGNORECASE)[0]
    action = _clean_bullet(action)
    overdue = bool(_OVERDUE_RE.search(deadline) or _OVERDUE_RE.search(line))
    return ParsedTask(
        action=action,
        assignee=assignee,
        deadline=deadline,
        timecode=timecode,
        overdue=overdue,
    )


def _is_bold_only(line: str) -> bool:
    s = line.strip()
    return s.startswith("**") and s.rstrip(":").endswith("**")


def _is_header_line(line: str) -> bool:
    return line.startswith("#") or _is_bold_only(line)


# ── Pandoc-converted protocol dialect (real Bitrix docx → gfm output) ─────────
#
# node_17_bitrix24_upload.js uploads DOCX built from Word paragraph styles, not
# markdown headings — pandoc's docx→gfm reader renders section labels as plain
# text lines (no ``#``/``##``) and metadata/tasks/risks as pipe tables instead
# of bold-annotated bullets. This dialect is detected and parsed separately;
# the original bold-header dialect (seed data, Nodul JSON `tasks`) is untouched.

_PANDOC_TITLE_MARKER = "протокол совещания"
_PANDOC_SECTION_LABELS = {
    "краткая сводка": "summary",
    "участники": "participants",
    "повестка": "agenda",
    "производство по направлениям": "skip",
    "резюме": "resume",
    "риски и эскалации": "risks",
    "задачи": "tasks",
    "задачи по сотрудникам": "skip",
    "метрики совещания": "skip",
}
_PANDOC_TIMECODE_RE = re.compile(r"\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*$")
_PANDOC_PLACEHOLDER_RE = re.compile(
    r"^—$|не (сформирован|определен|назначен)\w*", re.IGNORECASE
)
# Pandoc escapes markdown-special punctuation in docx→gfm output (e.g. "\[",
# "\_") — strip the backslash so downstream text/placeholder matching sees the
# literal characters.
_PANDOC_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+.!-])")


def _unescape_pandoc(s: str) -> str:
    return _PANDOC_ESCAPE_RE.sub(r"\1", s)


def _looks_like_pandoc_protocol(text: str) -> bool:
    head = text[:200].lower()
    return _PANDOC_TITLE_MARKER in head and "\n|" in text


def _split_paragraphs(text: str) -> list[list[str]]:
    """Split text into blank-line-delimited paragraph blocks (each a line list).

    Pandoc reflows wrapped bullet/paragraph text across physical lines without
    repeating the ``•`` marker, but always separates logical units (section
    labels, bullets, table blocks) with a blank line — so blank-line splitting
    is the reliable paragraph boundary for this dialect.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw in text.splitlines():
        line = _unescape_pandoc(raw.strip())
        if not line:
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _is_pandoc_placeholder(text: str) -> bool:
    return bool(_PANDOC_PLACEHOLDER_RE.search(text.strip()))


def _parse_pipe_table(lines: list[str]) -> list[list[str]]:
    """Parse pipe-table lines into cleaned cell rows, dropping separator rows."""
    rows: list[list[str]] = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        cells = [re.sub(r"^\*\*(.*)\*\*$", r"\1", c).strip() for c in cells]
        rows.append(cells)
    return rows


def _clean_pandoc_bullet(joined: str) -> tuple[str, str]:
    text = re.sub(r"^[•]\s*", "", joined).strip()
    m = _PANDOC_TIMECODE_RE.search(text)
    timecode = ""
    if m:
        timecode = m.group(1)
        text = text[: m.start()].strip()
    return text, timecode


def _summary_from_pandoc_table(rows: list[list[str]]) -> str:
    kv = {row[0].rstrip(":").strip(): row[1].strip() for row in rows if len(row) >= 2}
    parts = []
    status = kv.get("Статус производства", "")
    if status and status != "—":
        parts.append(status)
    problems = kv.get("Критические проблемы", "")
    if problems and problems != "—":
        parts.append(f"Проблемы: {problems}")
    rec = kv.get("Рекомендация", "")
    if rec and rec != "—":
        parts.append(f"Рекомендация: {rec}")
    return ". ".join(parts)


def _participants_from_pandoc_table(rows: list[list[str]]) -> list[str]:
    names = []
    for row in rows[1:]:  # rows[0] is the header (№ / ФИО / Роль)
        if len(row) < 2:
            continue
        name = row[1].strip()
        if name and name != "—" and not _is_pandoc_placeholder(name):
            names.append(name)
    return names


def _risks_from_pandoc_table(rows: list[list[str]], pm: ParsedMeeting) -> None:
    for row in rows[1:]:  # rows[0] is the header (Риск / Влияние / Статус / Эскалация)
        if len(row) < 4 or row[0] in ("", "—"):
            continue
        risk, impact, escalation = row[0], row[1], row[3]
        text = f"{risk} — {impact}" if impact and impact != "—" else risk
        if escalation.strip().lower() == "да":
            pm.tasks.append(ParsedTask(action=text, status="escalation"))
        else:
            pm.decisions.append(text)


def _tasks_from_pandoc_table(rows: list[list[str]], pm: ParsedMeeting) -> None:
    for row in rows[1:]:  # rows[0] is the header (№ / Задача / Ответственный / Срок / Приоритет)
        if len(row) < 2 or row[0] in ("", "—"):
            continue
        action = row[1].strip() if len(row) > 1 else ""
        if not action or action == "—":
            continue
        assignee = row[2].strip() if len(row) > 2 else ""
        deadline = row[3].strip() if len(row) > 3 else ""
        pm.tasks.append(
            ParsedTask(
                action=action,
                assignee="" if assignee == "—" else assignee,
                deadline="" if deadline == "—" else deadline,
                overdue=bool(_OVERDUE_RE.search(deadline)),
            )
        )


def _parse_pandoc_protocol(text: str) -> ParsedMeeting:
    blocks = _split_paragraphs(text)
    pm = ParsedMeeting()
    i = 0

    if i < len(blocks) and len(blocks[i]) == 1 and blocks[i][0].strip().lower() == _PANDOC_TITLE_MARKER:
        i += 1
    if i < len(blocks) and len(blocks[i]) == 1 and not blocks[i][0].startswith("|"):
        pm.title = blocks[i][0].strip()
        i += 1
    if i < len(blocks) and blocks[i][0].startswith("|"):  # metadata table (Дата/Время/...)
        i += 1

    section: Optional[str] = None
    current_theme = ""
    agenda_points: list[ParsedPoint] = []

    while i < len(blocks):
        block = blocks[i]
        i += 1
        first = block[0]

        if len(block) == 1 and not first.startswith("|") and not first.startswith("•"):
            label = first.strip().rstrip(":").lower()
            if label in _PANDOC_SECTION_LABELS:
                section = _PANDOC_SECTION_LABELS[label]
                current_theme = ""
                continue

        if first.startswith("|"):
            rows = _parse_pipe_table(block)
            if section == "summary":
                pm.summary = _summary_from_pandoc_table(rows)
            elif section == "participants":
                pm.meta["speakers"] = _participants_from_pandoc_table(rows)
            elif section == "risks":
                _risks_from_pandoc_table(rows, pm)
            elif section == "tasks":
                _tasks_from_pandoc_table(rows, pm)
            continue

        joined = " ".join(block)
        if _is_pandoc_placeholder(joined):
            continue

        if section == "resume":
            if first.startswith("•"):
                text_clean, timecode = _clean_pandoc_bullet(joined)
                if text_clean:
                    pm.points.append(ParsedPoint(text=text_clean, theme=current_theme, timecode=timecode))
                    if current_theme and current_theme not in pm.themes:
                        pm.themes.append(current_theme)
            else:
                current_theme = joined
        elif section == "agenda" and first.startswith("•"):
            text_clean, timecode = _clean_pandoc_bullet(joined)
            if text_clean:
                agenda_points.append(ParsedPoint(text=text_clean, timecode=timecode))

    if not pm.points and agenda_points:
        pm.points = agenda_points
    if not pm.title:
        pm.title = "Совещание"
    return pm


def parse_protocol(text: str) -> ParsedMeeting:
    """Parse a meeting-protocol markdown document into a ``ParsedMeeting``."""
    if _looks_like_pandoc_protocol(text):
        return _parse_pandoc_protocol(text)

    meta, body = _strip_frontmatter(text)
    speakers = meta.get("speakers") or []
    if isinstance(speakers, str):
        speakers = [s.strip() for s in speakers.split(",") if s.strip()]

    pm = ParsedMeeting(meta=meta, title=str(meta.get("title", "")).strip())

    block: Optional[str] = None
    current_theme = ""
    summary_lines: list[str] = []

    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue

        if stripped.startswith("## "):
            current_theme = stripped[3:].strip().rstrip(".")
            if current_theme and current_theme not in pm.themes:
                pm.themes.append(current_theme)
            continue

        kind = _block_kind(stripped) if _is_header_line(stripped) else None
        if kind:
            block = kind
            continue

        if block is None and not pm.title and _is_bold_only(stripped):
            pm.title = stripped.strip("*# ").strip()
            continue

        is_bullet = bool(re.match(r"^[-*•]\s+", stripped))

        if block == "summary":
            summary_lines.append(_clean_bullet(stripped) if is_bullet else stripped)
        elif block == "themes" and is_bullet:
            text_clean = _clean_bullet(stripped)
            if text_clean:
                pm.points.append(
                    ParsedPoint(
                        text=text_clean,
                        theme=current_theme,
                        speaker=_detect_speaker(text_clean, speakers),
                        timecode=_timecode(stripped),
                    )
                )
        elif block == "decisions" and is_bullet:
            dec = _clean_bullet(stripped)
            if dec:
                pm.decisions.append(dec)
        elif block == "tasks" and is_bullet:
            task = _parse_task_bullet(stripped)
            if task.action:
                pm.tasks.append(task)

    pm.summary = " ".join(s for s in summary_lines if s).strip()
    if not pm.title:
        pm.title = str(meta.get("title") or "Совещание").strip()
    return pm


# ── Mapping to store records ───────────────────────────────────────────────────


def _year_from_date(date_str: str) -> Optional[int]:
    m = re.search(r"(\d{4})", str(date_str or ""))
    return int(m.group(1)) if m else None


def _slug(value: str) -> str:
    # Long enough that "<long site name>-<date>-<time>" never collides two
    # distinct meetings into the same source_id by truncating away the
    # differentiating time suffix (was 48 — too tight for real site names;
    # e.g. "ОМС Заготовительное производство" + date + time collapsed two
    # same-day meetings at different times to one source_id).
    value = re.sub(r"[^\w]+", "-", value.lower(), flags=re.UNICODE).strip("-")
    return value[:96] or "meeting"


def build_records(pm: ParsedMeeting, stem: str) -> tuple[str, dict, list[dict]]:
    """Return ``(source_id, meeting_meta, fragments)`` for a parsed meeting."""
    meta = pm.meta
    date_str = str(meta.get("date") or "")
    site = str(meta.get("site") or "")
    mtype = str(meta.get("type") or "")
    base = stem if (date_str and date_str in stem) else f"{date_str}-{stem}"
    source_id = "mtg-" + _slug(base)

    meeting_meta = {
        "date": date_str,
        "type": mtype,
        "site": site,
        "time": str(meta.get("time") or ""),
        "duration": meta.get("duration"),
        "speakers": meta.get("speakers") or [],
        "title": pm.title,
        "decisions": pm.decisions,
        "summary": pm.summary,
    }

    fragments: list[dict] = []
    for p in pm.points:
        fragments.append(
            {
                "text": p.text,
                "type": "summary",
                "section": p.theme or site,
                "relevance": 3,
                "citation_intent": "summary",
                "usage_hint": json.dumps(
                    {"speaker": p.speaker, "timecode": p.timecode},
                    ensure_ascii=False,
                ),
            }
        )
    for d in pm.decisions:
        fragments.append(
            {
                "text": d,
                "type": "decision",
                "section": site,
                "relevance": 4,
                "citation_intent": "decision",
                "usage_hint": json.dumps({}, ensure_ascii=False),
            }
        )
    for t in pm.tasks:
        fragments.append(
            {
                "text": t.action,
                "type": "task",
                "section": site or mtype,
                "relevance": 4 if t.overdue else 3,
                "citation_intent": "escalation" if t.overdue else (t.status or "new"),
                "usage_hint": json.dumps(
                    {
                        "assignee": t.assignee,
                        "deadline": t.deadline,
                        "timecode": t.timecode,
                        "overdue": t.overdue,
                        "status": t.status,
                    },
                    ensure_ascii=False,
                ),
            }
        )
    return source_id, meeting_meta, fragments


# ── Store writes ────────────────────────────────────────────────────────────


def _ensure_meeting_meta_column(state) -> None:
    """Add a nullable ``meeting_meta TEXT`` column if absent (this DB only).

    Guarded ALTER — never touches ``StateManager._migrate_schema``/user_version.
    """
    with state._conn() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}
        if "meeting_meta" not in cols:
            conn.execute("ALTER TABLE sources ADD COLUMN meeting_meta TEXT")


def _set_meeting_meta(state, source_id: str, meeting_meta: dict) -> None:
    with state._conn() as conn:
        conn.execute(
            "UPDATE sources SET meeting_meta=? WHERE id=?",
            (json.dumps(meeting_meta, ensure_ascii=False), source_id),
        )


def parse_nodul_payload(payload: dict) -> tuple[str, ParsedMeeting]:
    """Adapt a Nodul/MyMeet webhook payload → ``(meeting_id, ParsedMeeting)``.

    Meeting metadata comes from payload fields (not frontmatter); summary /
    themes / decisions are parsed from ``protocol_md``; **tasks are taken from
    the structured ``payload["tasks"]``** (already extracted by the task-agent),
    falling back to MD-parsed tasks only when the structured list is absent.
    """
    protocol_md = str(payload.get("protocol_md") or "")
    pm = parse_protocol(protocol_md)

    speakers = payload.get("speakers") or pm.meta.get("speakers") or []
    if isinstance(speakers, str):
        speakers = [s.strip() for s in speakers.split(",") if s.strip()]

    pm.meta = {
        "date": str(payload.get("date") or ""),
        "type": str(payload.get("type") or ""),
        "site": str(payload.get("site") or ""),
        "time": str(payload.get("time") or ""),
        "duration": payload.get("duration"),
        "speakers": speakers,
    }
    if payload.get("title"):
        pm.title = str(payload["title"]).strip()

    # Re-detect speakers on points now that the speaker list is known (the body
    # had no frontmatter, so parse_protocol couldn't attribute them).
    for p in pm.points:
        if not p.speaker:
            p.speaker = _detect_speaker(p.text, speakers)

    tasks = payload.get("tasks")
    if tasks:
        pm.tasks = []
        for t in tasks:
            deadline = str(t.get("deadline") or "").strip()
            pm.tasks.append(
                ParsedTask(
                    action=str(t.get("action") or "").strip(),
                    assignee=str(t.get("assignee") or "").strip(),
                    deadline=deadline,
                    timecode=str(t.get("timecode") or ""),
                    status=str(t.get("status") or "new"),
                    overdue=bool(_OVERDUE_RE.search(deadline)),
                )
            )
        pm.tasks = [t for t in pm.tasks if t.action]

    return str(payload.get("meeting_id") or ""), pm


def ingest_meeting(state, embeddings, payload: dict) -> dict:
    """Ingest one meeting from a Nodul payload (idempotent, replace-on-reingest)."""
    meeting_id, pm = parse_nodul_payload(payload)
    stem = meeting_id or pm.title
    source_id, _, _ = build_records(pm, stem)
    _ensure_meeting_meta_column(state)
    # Replace-on-reingest: a re-sent (re-generated) protocol fully replaces the
    # previous fragments instead of appending duplicates.
    state.delete_fragments(source_id)
    return import_meeting(state, embeddings, pm, stem)


def _embed_texts(embeddings, texts: list[str]) -> list:
    """Batch-embed texts, falling back to per-item ``embed`` when a backend
    (e.g. OpenAIEmbeddings) does not implement ``embed_batch``."""
    if hasattr(embeddings, "embed_batch"):
        try:
            return embeddings.embed_batch(texts)
        except (AttributeError, NotImplementedError):
            pass
    return [embeddings.embed(t) for t in texts]


def import_meeting(state, embeddings, pm: ParsedMeeting, stem: str) -> dict:
    """Write one parsed meeting to the store and embed its fragments."""
    source_id, meeting_meta, frags = build_records(pm, stem)

    _ensure_meeting_meta_column(state)
    # Resolve the portal site slug once at write time (this is the common path
    # for both seed import and webhook ingest). Empty registry → '' (a later
    # /meetings/sites/sync remaps all meetings).
    ensure_portal_tables(state)
    sites = get_sites(state)
    meeting_meta["site_slug"] = (
        resolve_site_slug(meeting_meta.get("site", ""), pm.title, sites) if sites else ""
    )
    state.register_sources([source_id])
    state.update_source_info(
        source_id,
        title=pm.title,
        authors="; ".join(meeting_meta.get("speakers") or []),
        year=_year_from_date(meeting_meta.get("date")),
        abstract=pm.summary,
        source_type="meeting",
    )
    state.update_source_metadata(source_id, status="completed")
    _set_meeting_meta(state, source_id, meeting_meta)
    state.save_fragments(source_id, frags)

    embedded = 0
    if embeddings is not None:
        rows = state.get_fragments(source_id=source_id, limit=100000)
        texts = [r["fragment_text"] for r in rows]
        if texts:
            vecs = _embed_texts(embeddings, texts)
            for r, vec in zip(rows, vecs):
                if vec:
                    state.save_fragment_embedding(r["id"], vec, embeddings.model_name)
                    embedded += 1
        src_vec = embeddings.embed(pm.title, pm.summary)
        if src_vec:
            state.save_embedding(source_id, src_vec, embeddings.model_name)

    return {
        "source_id": source_id,
        "fragments": len(frags),
        "tasks": len(pm.tasks),
        "embedded": embedded,
    }


# ── Bridge: project root → (StateManager, embeddings) ─────────────────────────

BONUM_ROOT_ENV = "KLEMMA_BONUM_PROJECT_ROOT"
_BRIDGE_CACHE: dict[str, tuple] = {}


def bonum_db_path(root) -> Path:
    return Path(root).expanduser() / ".klemma" / "data" / "klemma.db"


def build_state_and_embeddings(root, *, use_cache: bool = False):
    """Build ``(StateManager, embeddings|None)`` for a meeting project root.

    Embeddings are built from the project's resolved config so that import-time
    and query-time use the identical model (``embedding_model`` must match for
    ``retrieve_similar_fragments``). Falls back to klemmarc embeddings if project
    config resolution fails.
    """
    key = str(Path(root).expanduser())
    if use_cache and key in _BRIDGE_CACHE:
        return _BRIDGE_CACHE[key]

    from .state import StateManager

    state = StateManager(str(bonum_db_path(root)))

    # Resolution order: explicit env (matches SaaS _create_embeddings_provider) →
    # project config → klemmarc. Import-time and query-time both go through here,
    # so embedding_model is identical as long as the same source wins.
    embeddings = (
        _embeddings_from_env()
        or _embeddings_from_project_config(root)
        or _embeddings_from_klemmarc()
    )

    result = (state, embeddings)
    if use_cache:
        _BRIDGE_CACHE[key] = result
    return result


def _embeddings_from_env():
    """Build embeddings from KLEMMA_EMBEDDINGS_* env (litellm/Ollama for the demo)."""
    import os

    backend = os.getenv("KLEMMA_EMBEDDINGS_BACKEND", "").strip()
    if not backend:
        return None
    from .embeddings import create_embeddings

    cfg = {
        "backend": backend,
        "model": os.getenv("KLEMMA_EMBEDDINGS_MODEL", ""),
        "base_url": os.getenv("KLEMMA_EMBEDDINGS_BASE_URL"),
        "timeout": int(os.getenv("KLEMMA_EMBEDDINGS_TIMEOUT", "60")),
    }
    dim = os.getenv("KLEMMA_EMBEDDINGS_DIM")
    if dim:
        cfg["dim"] = int(dim)
    return create_embeddings(cfg)


def _embeddings_from_project_config(root):
    """Build embeddings from the project's resolved klemma config."""
    try:
        from .config import discover_project_chain, resolve_effective_config
        from .embeddings import create_embeddings

        chain = discover_project_chain(Path(root).expanduser()) or [
            Path(root).expanduser()
        ]
        cfg, _, _ = resolve_effective_config(chain)
        if cfg.embeddings.backend:
            return create_embeddings(
                cfg.embeddings.model_dump(),
                api_keys=cfg.ai._resolved_api_keys or None,
            )
    except Exception:
        pass
    return None


def _embeddings_from_klemmarc():
    """Fallback embeddings provider straight from ~/.klemmarc.yaml."""
    try:
        from .config import _load_klemmarc
        from .embeddings import create_embeddings

        rc = _load_klemmarc() or {}
        emb = rc.get("embeddings") or {}
        if not emb.get("backend"):
            return None
        return create_embeddings(emb, api_keys=rc.get("api_keys") or None)
    except Exception:
        return None


def _ai_from_env():
    """Build an AI provider from env keys (server/container path).

    Used only when an env model is set AND the matching provider key is in env
    (e.g. ANTHROPIC_API_KEY in the bonum container). Returns ``(None, "")``
    otherwise so callers fall back to project config (local CLI via klemmarc).
    """
    import os

    model = os.getenv("KLEMMA_BONUM_AI_MODEL") or os.getenv("KLEMMA_AI_MODEL") or ""
    if not model:
        return None, ""
    provider = model.split("/", 1)[0] if "/" in model else "anthropic"
    env_key = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider)
    if not env_key or not os.getenv(env_key):
        return None, ""
    from .ai import create_ai
    from .config import AIConfig

    cfg = AIConfig(backend="litellm", model=model)
    cfg._resolved_api_keys = {provider: os.environ[env_key]}
    return create_ai(cfg), model


def build_ai(root):
    """Build ``(ai_provider, model_name)`` for ``/ask``.

    Order: env keys (container/server) → project config (local CLI via klemmarc).
    Returns ``(None, "")`` on failure.
    """
    ai, model = _ai_from_env()
    if ai is not None:
        return ai, model
    try:
        import os

        from .ai import create_ai
        from .config import discover_project_chain, resolve_effective_config

        chain = discover_project_chain(Path(root).expanduser()) or [
            Path(root).expanduser()
        ]
        cfg, _, _ = resolve_effective_config(chain)
        model = os.getenv("KLEMMA_BONUM_AI_MODEL") or cfg.ai.model
        cfg.ai.model = model
        return create_ai(cfg.ai), model
    except Exception:
        return None, ""


# ── Read-only query / aggregation helpers (used by the API routes) ────────────


def _loads(blob: Optional[str]) -> dict:
    try:
        d = json.loads(blob) if blob else {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _initials(name: str) -> str:
    parts = [p for p in re.split(r"[\s.]+", name.strip()) if p]
    return ("".join(p[0] for p in parts[:2])).upper() or "?"


def _meeting_meta_map(state) -> dict[str, dict]:
    """Return ``{source_id: meeting_meta_dict}`` for all meeting sources."""
    with state._conn() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sources)")}
        if "meeting_meta" not in cols:
            return {}
        rows = conn.execute(
            "SELECT id, meeting_meta FROM sources WHERE source_type='meeting'"
        ).fetchall()
    return {r[0]: _loads(r[1]) for r in rows}


def _days_cutoff(days: Optional[int]) -> Optional[str]:
    """ISO date string for the window start, or None when no days filter."""
    if days is None:
        return None
    return (date.today() - timedelta(days=days)).isoformat()


def _passes_filters(meta: dict, sites: Optional[set[str]], cutoff: Optional[str]) -> bool:
    """Site/days filter over one meeting_meta.

    A meeting with an unresolved slug ('') passes only when ``sites`` is None
    (director view); meetings without a date are skipped only when a days
    window is set (ISO string compare needs a date).
    """
    if sites is not None and meta.get("site_slug", "") not in sites:
        return False
    if cutoff is not None:
        date_str = str(meta.get("date") or "")
        if not date_str or date_str < cutoff:
            return False
    return True


def count_meetings_by_site(state, days: int = 90) -> dict[str, int]:
    """Per-slug meeting counts within the last ``days`` (unresolved excluded)."""
    cutoff = _days_cutoff(days)
    counts: dict[str, int] = {}
    for meta in _meeting_meta_map(state).values():
        if not _passes_filters(meta, None, cutoff):
            continue
        slug = meta.get("site_slug", "")
        if slug:
            counts[slug] = counts.get(slug, 0) + 1
    return counts


def list_meetings(
    state, *, sites: Optional[set[str]] = None, days: Optional[int] = None
) -> dict:
    """Build the Совещания screen payload: per-meeting cards + headline stats."""
    metas = _meeting_meta_map(state)
    display = site_display_names(state)
    cutoff = _days_cutoff(days)
    meetings = []
    total_tasks = total_escalations = 0
    for sid, meta in metas.items():
        if not _passes_filters(meta, sites, cutoff):
            continue
        frags = state.get_fragments(source_id=sid, limit=100000)
        task_list = []
        overdue = escalation = 0
        for f in frags:
            if f["fragment_type"] != "task":
                continue
            h = _loads(f["usage_hint"])
            is_overdue = bool(h.get("overdue"))
            is_esc = f.get("citation_intent") == "escalation"
            overdue += 1 if is_overdue else 0
            escalation += 1 if is_esc else 0
            task_list.append(
                {
                    "title": f["fragment_text"],
                    "who": h.get("assignee", ""),
                    "due": h.get("deadline", ""),
                    "overdue": is_overdue,
                    "time": h.get("timecode", ""),
                }
            )
        total_tasks += len(task_list)
        total_escalations += escalation
        chips = []
        if overdue:
            chips.append({"label": f"{overdue} просрочено", "tone": "warn"})
        if escalation:
            chips.append({"label": f"{escalation} эскалация", "tone": "err"})
        if not chips:
            chips.append({"label": "в работе", "tone": "ok"})
        speakers = meta.get("speakers") or []
        # Resolved slug → registry display name; unresolved → raw meta string.
        site_label = display.get(meta.get("site_slug", "")) or meta.get("site", "")
        meetings.append(
            {
                "id": sid,
                "date": meta.get("date", ""),
                "type": meta.get("type", ""),
                "site": site_label,
                "time": meta.get("time", ""),
                "title": meta.get("title", ""),
                "tasks": len(task_list),
                "speakers": [_initials(s) for s in speakers],
                "chips": chips,
                "summary": meta.get("summary", ""),
                "decisions": meta.get("decisions", []),
                "task_list": task_list,
            }
        )
    meetings.sort(key=lambda m: (m["date"], m["time"]), reverse=True)
    return {
        "meetings": meetings,
        "stats": {
            "meetings": len(meetings),
            "tasks": total_tasks,
            "escalations": total_escalations,
        },
    }


def search_meetings(
    state,
    embeddings,
    query: str,
    top_k: int = 8,
    *,
    sites: Optional[set[str]] = None,
) -> dict:
    """Semantic search across meeting fragments + keyword-overlap comparison."""
    if embeddings is None or not query.strip():
        return {"query": query, "results": [], "semantic_count": 0, "keyword_count": 0}
    metas = _meeting_meta_map(state)
    qv = embeddings.embed(query, "")
    # Over-retrieve so a site post-filter still fills top_k. With a site filter
    # the global top can be dominated by literal matches from OTHER sites, so
    # the multiplier must be much larger than the unfiltered case needs.
    over = top_k * 12 if sites is not None else top_k * 4
    hits = (
        state.retrieve_similar_fragments(qv, top_k=over, model=embeddings.model_name)
        if qv
        else []
    )
    if sites is not None:
        hits = [h for h in hits if metas.get(h["source_id"], {}).get("site_slug", "") in sites]
    hits = hits[:top_k]
    words = [w for w in re.split(r"\W+", query.lower()) if len(w) > 2]
    keyword_count = 0
    results = []
    for h in hits:
        meta = metas.get(h["source_id"], {})
        hint = _loads(h["usage_hint"])
        text_low = h["fragment_text"].lower()
        if any(w in text_low for w in words):
            keyword_count += 1
        results.append(
            {
                "quote": h["fragment_text"],
                "score": h.get("similarity", 0.0),
                "speaker": hint.get("speaker", ""),
                "meeting": meta.get("title", ""),
                "type": meta.get("type", ""),
                "site": meta.get("site", ""),
                "date": meta.get("date", ""),
                "time": hint.get("timecode", ""),
                "citekey": h["source_id"],
                "tag": h.get("citation_intent", ""),
            }
        )
    return {
        "query": query,
        "results": results,
        "semantic_count": len(results),
        "keyword_count": keyword_count,
    }


def aggregate_tasks(
    state, *, sites: Optional[set[str]] = None, days: Optional[int] = None
) -> dict:
    """Build the Задачи screen payload: stats, recurring themes, overdue, escalations."""
    metas = _meeting_meta_map(state)
    display = site_display_names(state)
    cutoff = _days_cutoff(days)
    metas = {
        sid: meta for sid, meta in metas.items() if _passes_filters(meta, sites, cutoff)
    }
    open_tasks = overdue = escalations_n = 0
    overdue_persons: dict[str, int] = {}
    overdue_sites: dict[str, int] = {}
    escalations: list[dict] = []
    # theme name → set of source_ids (recurring across meetings)
    theme_meetings: dict[str, set] = {}

    for sid, meta in metas.items():
        site = meta.get("site", "")
        # Overdue-by-site ranking uses registry display names when resolved.
        site_label = display.get(meta.get("site_slug", "")) or site
        frags = state.get_fragments(source_id=sid, limit=100000)
        for f in frags:
            if f["fragment_type"] == "summary" and f.get("section"):
                theme_meetings.setdefault(f["section"], set()).add(sid)
            if f["fragment_type"] != "task":
                continue
            open_tasks += 1
            h = _loads(f["usage_hint"])
            if h.get("overdue"):
                overdue += 1
                who = h.get("assignee") or "—"
                overdue_persons[who] = overdue_persons.get(who, 0) + 1
                overdue_sites[site_label] = overdue_sites.get(site_label, 0) + 1
            if f.get("citation_intent") == "escalation":
                escalations_n += 1
                escalations.append(
                    {
                        "title": f["fragment_text"],
                        "owner": h.get("assignee", ""),
                        "site": site,
                        "age": h.get("deadline", ""),
                    }
                )

    themes = []
    for name, sids in theme_meetings.items():
        if len(sids) < 2:
            continue
        srcs = []
        for s in sids:
            m = metas.get(s, {})
            srcs.append(
                {"date": m.get("date", ""), "type": m.get("type", ""), "site": m.get("site", "")}
            )
        escalated = any(
            e for e in escalations if any(w in e["title"].lower() for w in name.lower().split())
        )
        themes.append(
            {
                "title": name,
                "count": len(sids),
                "escalated": escalated,
                "meetings": sorted(srcs, key=lambda x: x["date"], reverse=True),
            }
        )
    themes.sort(key=lambda t: t["count"], reverse=True)

    def _ranked(d: dict[str, int]) -> list[dict]:
        items = sorted(d.items(), key=lambda kv: kv[1], reverse=True)
        top = items[0][1] if items else 1
        return [{"name": k, "n": v, "pct": f"{round(100 * v / top)}%"} for k, v in items]

    return {
        "stats": [
            {"n": open_tasks, "label": "открытых задач", "tone": "ink"},
            {"n": overdue, "label": "просрочено", "tone": "cta"},
            {"n": escalations_n, "label": "эскалаций", "tone": "err"},
            {"n": len(metas), "label": "совещаний", "tone": "ink"},
        ],
        "themes": themes,
        "overdue_persons": _ranked(overdue_persons),
        "overdue_sites": _ranked(overdue_sites),
        "escalations": escalations,
    }


def _prompts_dir() -> Path:
    """Shipped prompts directory: KLEMMA_PROMPTS_DIR (container) or dev layout."""
    env = os.environ.get("KLEMMA_PROMPTS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_qa_prompt() -> str:
    path = _prompts_dir() / "meeting_qa.md"
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return (
            "Ответь на вопрос только на основе фрагментов ниже, ставь ссылки [n], "
            "не выдумывай.\n{% for f in fragments %}[{{f.n}}] {{f.text}} "
            "({{f.meeting}}, {{f.time}})\n{% endfor %}\nВопрос: {{query}}"
        )


def answer_question(
    state,
    embeddings,
    ai,
    model: str,
    query: str,
    top_k: int = 10,
    *,
    sites: Optional[set[str]] = None,
) -> dict:
    """RAG answer over meeting fragments with cited sources."""
    metas = _meeting_meta_map(state)
    if embeddings is None or not query.strip():
        return {"answer": "", "model": model, "sources": [], "followups": []}
    qv = embeddings.embed(query, "")
    # Over-retrieve so a site post-filter still fills the context window (same
    # rationale as search_meetings: a site's fragments can be crowded out of
    # the global top by literal matches from other sites).
    over = top_k * 12 if sites is not None else top_k * 4
    hits = (
        state.retrieve_similar_fragments(qv, top_k=over, model=embeddings.model_name)
        if qv
        else []
    )
    if sites is not None:
        hits = [h for h in hits if metas.get(h["source_id"], {}).get("site_slug", "") in sites]
    hits = hits[:top_k]
    frags = []
    sources = []
    for i, h in enumerate(hits, 1):
        meta = metas.get(h["source_id"], {})
        hint = _loads(h["usage_hint"])
        frags.append(
            {
                "n": i,
                "text": h["fragment_text"],
                "meeting": meta.get("title", ""),
                "type": meta.get("type", ""),
                "site": meta.get("site", ""),
                "date": meta.get("date", ""),
                "time": hint.get("timecode", ""),
                "speaker": hint.get("speaker", ""),
            }
        )
        sources.append(
            {
                "n": i,
                "quote": h["fragment_text"],
                "meeting": meta.get("title", ""),
                "date": meta.get("date", ""),
                "time": hint.get("timecode", ""),
                "speaker": hint.get("speaker", ""),
                "citekey": h["source_id"],
            }
        )

    answer = ""
    if ai is not None and frags:
        from jinja2.sandbox import SandboxedEnvironment

        system = SandboxedEnvironment().from_string(_load_qa_prompt()).render(
            fragments=frags, query=query
        )
        try:
            result = ai.call(system=system, user=query, max_tokens=1200)
            answer = result if isinstance(result, str) else getattr(result, "text", "") or ""
        except Exception as e:  # pragma: no cover - network/runtime
            answer = f"(ошибка генерации ответа: {e})"

    return {
        "answer": answer.strip(),
        "model": model,
        "sources": sources,
        "followups": [
            "Что из этого просрочено?",
            "Кто отвечает за эти задачи?",
            "В каких совещаниях это обсуждалось?",
        ],
    }
