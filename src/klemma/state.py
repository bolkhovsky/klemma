"""Unified SQLite state manager."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class ProcessingStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    zotero_key TEXT,
    status TEXT DEFAULT 'pending',
    processed_at TEXT,
    error_message TEXT,
    note_path TEXT,
    quality_score INTEGER,
    primary_chapter INTEGER,
    primary_section TEXT,
    relevance_nr1 INTEGER DEFAULT 0,
    relevance_nr2 INTEGER DEFAULT 0,
    citation_priority TEXT DEFAULT 'medium',
    pdf_path TEXT,
    pdf_text_length INTEGER,
    fragment_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_batches (
    date TEXT PRIMARY KEY,
    count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fragments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id),
    fragment_text TEXT NOT NULL,
    fragment_type TEXT,
    chapter INTEGER,
    section TEXT,
    relevance_score INTEGER,
    usage_hint TEXT,
    page_number INTEGER,
    extracted_at TEXT DEFAULT (datetime('now')),
    used_in_draft BOOLEAN DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_plans (
    date TEXT PRIMARY KEY,
    dissertation_task TEXT,
    assistant_task TEXT,
    reading_target TEXT,
    reading_snippet TEXT,
    progress_summary TEXT,
    plan_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    completed_tasks TEXT
);

CREATE TABLE IF NOT EXISTS reading_queue (
    source_id TEXT PRIMARY KEY REFERENCES sources(id),
    priority INTEGER DEFAULT 50,
    status TEXT DEFAULT 'queued',
    current_position INTEGER DEFAULT 0,
    total_length INTEGER,
    added_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS source_sections (
    source_id TEXT NOT NULL REFERENCES sources(id),
    chapter INTEGER NOT NULL,
    section TEXT NOT NULL,
    PRIMARY KEY (source_id, section)
);

CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(status);
CREATE INDEX IF NOT EXISTS idx_sources_chapter ON sources(primary_chapter);
CREATE INDEX IF NOT EXISTS idx_source_sections_section ON source_sections(section);
CREATE INDEX IF NOT EXISTS idx_source_sections_chapter ON source_sections(chapter);
CREATE INDEX IF NOT EXISTS idx_fragments_source ON fragments(source_id);
CREATE INDEX IF NOT EXISTS idx_fragments_section ON fragments(section);
CREATE INDEX IF NOT EXISTS idx_fragments_type ON fragments(fragment_type);
CREATE INDEX IF NOT EXISTS idx_reading_queue_priority ON reading_queue(priority DESC);

CREATE TABLE IF NOT EXISTS reference_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id),
    ref_authors TEXT NOT NULL,
    ref_year INTEGER,
    ref_title TEXT NOT NULL,
    why_relevant TEXT,
    dissertation_sections TEXT,
    status TEXT DEFAULT 'open',
    resolved_citekey TEXT,
    found_at TEXT DEFAULT (datetime('now')),
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reference_gaps_source ON reference_gaps(source_id);
CREATE INDEX IF NOT EXISTS idx_reference_gaps_status ON reference_gaps(status);

CREATE TABLE IF NOT EXISTS discoveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT NOT NULL,
    source_type TEXT NOT NULL,
    external_id TEXT,
    title TEXT,
    authors TEXT,
    year INTEGER,
    abstract TEXT,
    relevance_score INTEGER,
    usage_type TEXT,
    priority TEXT,
    matched_gap_id INTEGER,
    status TEXT DEFAULT 'pending',
    raw_data TEXT,
    discovered_at TEXT DEFAULT (datetime('now')),
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_discoveries_section ON discoveries(section);
CREATE INDEX IF NOT EXISTS idx_discoveries_status ON discoveries(status);

CREATE TABLE IF NOT EXISTS prune_verdicts (
    source_id TEXT PRIMARY KEY REFERENCES sources(id),
    verdict TEXT NOT NULL,
    reason TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

PRUNE_EXPIRY_DAYS = 14
PRUNE_DROP_SUBQUERY = (
    "SELECT source_id FROM prune_verdicts "
    "WHERE verdict='drop' AND updated_at > datetime('now', '-14 days')"
)


class StateManager:
    """Unified SQLite state for sources, fragments, plans, reading queue."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ── Sources ──────────────────────────────────────────────────────────

    def register_sources(self, source_ids: list[str]):
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO sources (id) VALUES (?)",
                [(sid,) for sid in source_ids],
            )

    def set_pdf_path(self, source_id: str, path: str):
        """Set the direct PDF path for a source."""
        with self._conn() as conn:
            conn.execute("UPDATE sources SET pdf_path = ? WHERE id = ?", (path, source_id))

    def get_pending_sources(self, limit: int = 0) -> list[str]:
        with self._conn() as conn:
            if limit > 0:
                today = date.today().isoformat()
                cur = conn.execute(
                    "SELECT count FROM daily_batches WHERE date = ?", (today,)
                )
                row = cur.fetchone()
                already = row["count"] if row else 0
                remaining = max(0, limit - already)
                if remaining == 0:
                    return []
                cur = conn.execute(
                    "SELECT id FROM sources WHERE status = ? ORDER BY id LIMIT ?",
                    (ProcessingStatus.PENDING.value, remaining),
                )
            else:
                cur = conn.execute(
                    "SELECT id FROM sources WHERE status = ? ORDER BY id",
                    (ProcessingStatus.PENDING.value,),
                )
            return [row["id"] for row in cur.fetchall()]

    def mark_processing(self, source_id: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE sources SET status = ? WHERE id = ?",
                (ProcessingStatus.PROCESSING.value, source_id),
            )

    def mark_completed(
        self,
        source_id: str,
        note_path: str,
        quality_score: int = 0,
        primary_chapter: Optional[int] = None,
        primary_section: Optional[str] = None,
        relevance_nr1: int = 0,
        relevance_nr2: int = 0,
        citation_priority: str = "medium",
    ):
        now = datetime.now().isoformat()
        today = date.today().isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE sources
                   SET status=?, processed_at=?, note_path=?, quality_score=?,
                       primary_chapter=?, primary_section=?,
                       relevance_nr1=?, relevance_nr2=?, citation_priority=?
                   WHERE id=?""",
                (
                    ProcessingStatus.COMPLETED.value, now, note_path, quality_score,
                    primary_chapter, primary_section,
                    relevance_nr1, relevance_nr2, citation_priority,
                    source_id,
                ),
            )
            conn.execute(
                """INSERT INTO daily_batches (date, count) VALUES (?, 1)
                   ON CONFLICT(date) DO UPDATE SET count = count + 1""",
                (today,),
            )

    def update_source_metadata(
        self,
        source_id: str,
        quality_score: int = 0,
        primary_chapter: Optional[int] = None,
        primary_section: Optional[str] = None,
        relevance_nr1: int = 0,
        relevance_nr2: int = 0,
        citation_priority: str = "medium",
        note_path: Optional[str] = None,
        status: str = "completed",
    ):
        """Set metadata on an existing source (e.g. from vault import)."""
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE sources
                   SET status=?, processed_at=?, quality_score=?,
                       primary_chapter=?, primary_section=?,
                       relevance_nr1=?, relevance_nr2=?, citation_priority=?,
                       note_path=COALESCE(?, note_path)
                   WHERE id=?""",
                (
                    status, now, quality_score,
                    primary_chapter, primary_section,
                    relevance_nr1, relevance_nr2, citation_priority,
                    note_path,
                    source_id,
                ),
            )

    def mark_failed(self, source_id: str, error: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE sources SET status=?, error_message=? WHERE id=?",
                (ProcessingStatus.FAILED.value, error, source_id),
            )

    def mark_skipped(self, source_id: str, reason: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE sources SET status=?, error_message=? WHERE id=?",
                (ProcessingStatus.SKIPPED.value, reason, source_id),
            )

    def get_source(self, source_id: str) -> Optional[dict]:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_stats(self) -> dict[str, int]:
        with self._conn() as conn:
            stats = {}
            for s in ProcessingStatus:
                cur = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sources WHERE status=?", (s.value,)
                )
                stats[s.value] = cur.fetchone()["cnt"]
            stats["total"] = sum(stats.values())
            today = date.today().isoformat()
            cur = conn.execute(
                "SELECT count FROM daily_batches WHERE date=?", (today,)
            )
            row = cur.fetchone()
            stats["today"] = row["count"] if row else 0
            return stats

    def set_source_sections(
        self, source_id: str, sections: list[str], chapters: list[int]
    ) -> None:
        """Записать все секции/главы для источника (из frontmatter sections/chapters)."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM source_sections WHERE source_id=?", (source_id,)
            )
            for sec in sections:
                # Определить главу из номера секции
                ch = int(sec.split(".")[0]) if "." in sec else None
                # Или взять из списка chapters если глава без точки
                if ch is None:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO source_sections (source_id, chapter, section) VALUES (?, ?, ?)",
                    (source_id, ch, sec),
                )
            # Добавить главы без конкретных секций (если chapters шире чем sections)
            existing_chapters = {int(s.split(".")[0]) for s in sections if "." in s}
            for ch in chapters:
                if ch not in existing_chapters:
                    conn.execute(
                        "INSERT OR IGNORE INTO source_sections (source_id, chapter, section) VALUES (?, ?, ?)",
                        (source_id, ch, str(ch)),
                    )

    def get_all_sources(self) -> list[dict]:
        """Get all completed sources with metadata."""
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT id, note_path, quality_score, primary_chapter,
                          primary_section, relevance_nr1, relevance_nr2,
                          citation_priority, fragment_count
                   FROM sources WHERE status=?
                   ORDER BY primary_chapter, citation_priority DESC, quality_score DESC""",
                (ProcessingStatus.COMPLETED.value,),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_by_chapter(self, chapter: int) -> list[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                f"""SELECT DISTINCT s.id, s.note_path, s.quality_score, s.primary_section,
                          s.relevance_nr1, s.relevance_nr2, s.citation_priority,
                          s.fragment_count
                   FROM sources s
                   LEFT JOIN source_sections ss ON s.id = ss.source_id
                   WHERE s.status=? AND (s.primary_chapter=? OR ss.chapter=?)
                     AND s.id NOT IN ({PRUNE_DROP_SUBQUERY})
                   ORDER BY s.citation_priority DESC, s.quality_score DESC""",
                (ProcessingStatus.COMPLETED.value, chapter, chapter),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_by_section(self, section: str) -> list[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                f"""SELECT DISTINCT s.id, s.note_path, s.quality_score, s.primary_chapter,
                          s.relevance_nr1, s.relevance_nr2, s.citation_priority,
                          s.fragment_count
                   FROM sources s
                   LEFT JOIN source_sections ss ON s.id = ss.source_id
                   WHERE s.status=? AND (s.primary_section LIKE ? OR ss.section LIKE ?)
                     AND s.id NOT IN ({PRUNE_DROP_SUBQUERY})
                   ORDER BY s.citation_priority DESC, s.quality_score DESC""",
                (ProcessingStatus.COMPLETED.value, f"{section}%", f"{section}%"),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_coverage_stats(self) -> dict:
        with self._conn() as conn:
            stats: dict = {"chapters": {}, "sections": {}, "nr1": {}, "nr2": {}}
            cur = conn.execute(
                """SELECT primary_chapter, COUNT(*) as cnt FROM sources
                   WHERE status=? AND primary_chapter IS NOT NULL
                   GROUP BY primary_chapter""",
                (ProcessingStatus.COMPLETED.value,),
            )
            for row in cur.fetchall():
                stats["chapters"][row["primary_chapter"]] = row["cnt"]

            cur = conn.execute(
                """SELECT primary_section, COUNT(*) as cnt FROM sources
                   WHERE status=? AND primary_section IS NOT NULL
                   GROUP BY primary_section""",
                (ProcessingStatus.COMPLETED.value,),
            )
            for row in cur.fetchall():
                stats["sections"][row["primary_section"]] = row["cnt"]

            for level in range(1, 6):
                cur = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sources WHERE status=? AND relevance_nr1>=?",
                    (ProcessingStatus.COMPLETED.value, level),
                )
                stats["nr1"][f">={level}"] = cur.fetchone()["cnt"]
                cur = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sources WHERE status=? AND relevance_nr2>=?",
                    (ProcessingStatus.COMPLETED.value, level),
                )
                stats["nr2"][f">={level}"] = cur.fetchone()["cnt"]
            return stats

    def get_gaps(self, min_sources: int = 3) -> list[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT primary_section, COUNT(*) as cnt FROM sources
                   WHERE status=? AND primary_section IS NOT NULL
                   GROUP BY primary_section HAVING cnt < ?
                   ORDER BY cnt ASC""",
                (ProcessingStatus.COMPLETED.value, min_sources),
            )
            return [
                {"section": row["primary_section"], "count": row["cnt"]}
                for row in cur.fetchall()
            ]

    def reset_non_completed(self) -> dict[str, int]:
        with self._conn() as conn:
            counts = {}
            for s in [ProcessingStatus.FAILED, ProcessingStatus.SKIPPED, ProcessingStatus.PROCESSING]:
                cur = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sources WHERE status=?", (s.value,)
                )
                counts[s.value] = cur.fetchone()["cnt"]
            conn.execute(
                "UPDATE sources SET status=?, error_message=NULL WHERE status IN (?,?,?)",
                (
                    ProcessingStatus.PENDING.value,
                    ProcessingStatus.FAILED.value,
                    ProcessingStatus.SKIPPED.value,
                    ProcessingStatus.PROCESSING.value,
                ),
            )
            return counts

    # ── Zotero Key / Rename ────────────────────────────────────────────────

    def get_zotero_key_map(self) -> dict[str, str]:
        """Return {zotero_key: citekey} for all sources with a populated zotero_key."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT id, zotero_key FROM sources WHERE zotero_key IS NOT NULL AND zotero_key != ''"
            )
            return {row["zotero_key"]: row["id"] for row in cur.fetchall()}

    def rename_source(self, old_id: str, new_id: str, zotero_key: str = ""):
        """Cascade-rename source across all tables."""
        with self._conn() as conn:
            # Temporarily disable FK checks for atomic rename
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute(
                "UPDATE sources SET id=?, zotero_key=? WHERE id=?",
                (new_id, zotero_key, old_id),
            )
            for table, col in [
                ("fragments", "source_id"),
                ("source_sections", "source_id"),
                ("reference_gaps", "source_id"),
                ("reading_queue", "source_id"),
                ("prune_verdicts", "source_id"),
            ]:
                conn.execute(
                    f"UPDATE {table} SET {col}=? WHERE {col}=?",
                    (new_id, old_id),
                )
            conn.execute("PRAGMA foreign_keys=ON")

    def delete_source(self, source_id: str):
        """Delete an orphan source and all its FK-dependent rows."""
        with self._conn() as conn:
            for table, col in [
                ("fragments", "source_id"),
                ("source_sections", "source_id"),
                ("reference_gaps", "source_id"),
                ("reading_queue", "source_id"),
                ("prune_verdicts", "source_id"),
            ]:
                conn.execute(f"DELETE FROM {table} WHERE {col}=?", (source_id,))
            conn.execute("DELETE FROM sources WHERE id=?", (source_id,))

    def populate_zotero_keys(self, mapping: dict[str, str]):
        """Backfill zotero_key from {citekey: itemKey}. Only updates NULL rows."""
        with self._conn() as conn:
            for citekey, item_key in mapping.items():
                conn.execute(
                    "UPDATE sources SET zotero_key=? WHERE id=? AND (zotero_key IS NULL OR zotero_key='')",
                    (item_key, citekey),
                )

    # ── Fragments ────────────────────────────────────────────────────────

    def save_fragments(self, source_id: str, fragments: list[dict]) -> int:
        """Save extracted fragments and update source fragment count."""
        with self._conn() as conn:
            for f in fragments:
                conn.execute(
                    """INSERT INTO fragments
                       (source_id, fragment_text, fragment_type, chapter, section,
                        relevance_score, usage_hint, page_number)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source_id,
                        f.get("text", ""),
                        f.get("type", "key_idea"),
                        f.get("chapter"),
                        f.get("section"),
                        f.get("relevance", 3),
                        f.get("usage_hint", ""),
                        f.get("page"),
                    ),
                )
            conn.execute(
                "UPDATE sources SET fragment_count=? WHERE id=?",
                (len(fragments), source_id),
            )
            return len(fragments)

    def get_fragments(
        self,
        source_id: Optional[str] = None,
        chapter: Optional[int] = None,
        section: Optional[str] = None,
        fragment_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query fragments with optional filters."""
        conditions = []
        params: list = []
        if source_id:
            conditions.append("source_id=?")
            params.append(source_id)
        if chapter:
            conditions.append("chapter=?")
            params.append(chapter)
        if section:
            conditions.append("section LIKE ?")
            params.append(f"{section}%")
        if fragment_type:
            conditions.append("fragment_type=?")
            params.append(fragment_type)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        with self._conn() as conn:
            cur = conn.execute(
                f"""SELECT f.*, s.id as citekey
                    FROM fragments f
                    JOIN sources s ON f.source_id = s.id
                    WHERE {where}
                    ORDER BY f.relevance_score DESC, f.extracted_at DESC
                    LIMIT ?""",
                params,
            )
            return [dict(row) for row in cur.fetchall()]

    def get_fragment_stats(self) -> dict:
        """Get fragment counts by type, chapter, section."""
        with self._conn() as conn:
            stats: dict = {"total": 0, "by_type": {}, "by_chapter": {}, "by_section": {}}
            cur = conn.execute("SELECT COUNT(*) as cnt FROM fragments")
            stats["total"] = cur.fetchone()["cnt"]

            cur = conn.execute(
                "SELECT fragment_type, COUNT(*) as cnt FROM fragments GROUP BY fragment_type"
            )
            for row in cur.fetchall():
                stats["by_type"][row["fragment_type"] or "unknown"] = row["cnt"]

            cur = conn.execute(
                "SELECT chapter, COUNT(*) as cnt FROM fragments WHERE chapter IS NOT NULL GROUP BY chapter"
            )
            for row in cur.fetchall():
                stats["by_chapter"][row["chapter"]] = row["cnt"]

            cur = conn.execute(
                "SELECT section, COUNT(*) as cnt FROM fragments WHERE section IS NOT NULL GROUP BY section"
            )
            for row in cur.fetchall():
                stats["by_section"][row["section"]] = row["cnt"]
            return stats

    # ── Daily Plans ──────────────────────────────────────────────────────

    def save_plan(
        self,
        dissertation_task: str,
        assistant_task: str,
        reading_target: str = "",
        reading_snippet: str = "",
        plan_json: str = "",
        progress_summary: str = "",
    ):
        today = date.today().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO daily_plans
                   (date, dissertation_task, assistant_task, reading_target,
                    reading_snippet, plan_json, progress_summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                   dissertation_task=?, assistant_task=?, reading_target=?,
                   reading_snippet=?, plan_json=?, progress_summary=?""",
                (
                    today, dissertation_task, assistant_task, reading_target,
                    reading_snippet, plan_json, progress_summary,
                    dissertation_task, assistant_task, reading_target,
                    reading_snippet, plan_json, progress_summary,
                ),
            )

    def get_writing_streak(self) -> dict:
        """Calculate writing streak and days without progress from daily_plans.

        A day counts as 'progress' if a plan was generated for it.
        Returns {"days_without_progress": int, "streak": int, "last_progress_date": str|None}.
        """
        from datetime import timedelta

        with self._conn() as conn:
            cur = conn.execute(
                "SELECT date FROM daily_plans ORDER BY date DESC LIMIT 30"
            )
            plan_dates = {row["date"] for row in cur.fetchall()}

        if not plan_dates:
            return {"days_without_progress": 0, "streak": 0, "last_progress_date": None}

        today = date.today()
        days_without = 0
        streak = 0
        last_progress = None

        # Count from yesterday backward (today's plan hasn't been generated yet)
        for i in range(1, 31):
            check = (today - timedelta(days=i)).isoformat()
            if check in plan_dates:
                last_progress = last_progress or check
                streak += 1
            else:
                if last_progress is None:
                    days_without += 1
                else:
                    break

        return {
            "days_without_progress": days_without,
            "streak": streak,
            "last_progress_date": last_progress,
        }

    def get_plan(self, plan_date: Optional[str] = None) -> Optional[dict]:
        d = plan_date or date.today().isoformat()
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM daily_plans WHERE date=?", (d,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_yesterday_plan(self) -> Optional[dict]:
        from datetime import timedelta
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        return self.get_plan(yesterday)

    # ── Reading Queue ────────────────────────────────────────────────────

    def add_to_reading_queue(self, source_id: str, priority: int = 50, total_length: int = 0):
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO reading_queue (source_id, priority, total_length)
                   VALUES (?, ?, ?)""",
                (source_id, priority, total_length),
            )

    def get_next_reading(self) -> Optional[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                f"""SELECT rq.*, s.id as citekey
                   FROM reading_queue rq
                   JOIN sources s ON rq.source_id = s.id
                   WHERE rq.status = 'queued'
                     AND s.id NOT IN ({PRUNE_DROP_SUBQUERY})
                   ORDER BY rq.priority DESC
                   LIMIT 1"""
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def update_reading_position(self, source_id: str, position: int):
        with self._conn() as conn:
            conn.execute(
                """UPDATE reading_queue SET current_position=?, status='reading',
                   started_at=COALESCE(started_at, datetime('now'))
                   WHERE source_id=?""",
                (position, source_id),
            )

    def complete_reading(self, source_id: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE reading_queue SET status='completed', completed_at=datetime('now') WHERE source_id=?",
                (source_id,),
            )

    # ── Reference Gaps ────────────────────────────────────────────────────

    def save_reference_gaps(self, source_id: str, gaps: list[dict]) -> int:
        """Save reference gaps for a source (idempotent: replaces old gaps)."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM reference_gaps WHERE source_id=?", (source_id,)
            )
            for g in gaps:
                sections = g.get("dissertation_sections", [])
                conn.execute(
                    """INSERT INTO reference_gaps
                       (source_id, ref_authors, ref_year, ref_title,
                        why_relevant, dissertation_sections)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        source_id,
                        g.get("ref_authors", g.get("authors", "")),
                        g.get("ref_year", g.get("year")),
                        g.get("ref_title", g.get("title", "")),
                        g.get("why_relevant", ""),
                        json.dumps(sections) if sections else None,
                    ),
                )
            return len(gaps)

    def get_reference_gaps(
        self,
        section: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Get open reference gaps aggregated by (authors, year, title).

        Returns list of dicts with: ref_authors, ref_year, ref_title,
        why_relevant, dissertation_sections, count, avg_quality, score,
        source_ids.
        """
        with self._conn() as conn:
            query = """
                SELECT
                    rg.ref_authors,
                    rg.ref_year,
                    rg.ref_title,
                    GROUP_CONCAT(DISTINCT rg.why_relevant) as why_relevant,
                    GROUP_CONCAT(DISTINCT rg.dissertation_sections) as dissertation_sections,
                    COUNT(DISTINCT rg.source_id) as count,
                    AVG(COALESCE(s.quality_score, 3)) as avg_quality,
                    GROUP_CONCAT(DISTINCT rg.source_id) as source_ids
                FROM reference_gaps rg
                JOIN sources s ON rg.source_id = s.id
                WHERE rg.status = 'open'
            """
            params: list = []
            if section:
                query += " AND rg.dissertation_sections LIKE ?"
                params.append(f'%"{section}%')

            query += """
                GROUP BY rg.ref_authors, rg.ref_year, rg.ref_title
                ORDER BY COUNT(DISTINCT rg.source_id) * AVG(COALESCE(s.quality_score, 3)) DESC
                LIMIT ?
            """
            params.append(limit)

            cur = conn.execute(query, params)
            results = []
            for row in cur.fetchall():
                r = dict(row)
                count = r["count"]
                avg_q = r["avg_quality"] or 3
                # section_weight: 2.0 if relevant to NR1/NR2 sections (2.x)
                sections_str = r.get("dissertation_sections") or ""
                section_weight = 2.0 if '"2.' in sections_str else 1.0
                r["score"] = round(count * avg_q * section_weight, 1)
                results.append(r)
            return results

    def get_gap_summary(self) -> dict:
        """Lightweight summary for status line: open count + top gap."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT COUNT(DISTINCT ref_authors || ref_year || ref_title) as cnt "
                "FROM reference_gaps WHERE status='open'"
            )
            open_count = cur.fetchone()["cnt"]

            top_ref = None
            top_count = 0
            if open_count > 0:
                cur = conn.execute(
                    """SELECT ref_authors, ref_year,
                              COUNT(DISTINCT source_id) as cnt
                       FROM reference_gaps WHERE status='open'
                       GROUP BY ref_authors, ref_year, ref_title
                       ORDER BY cnt DESC LIMIT 1"""
                )
                row = cur.fetchone()
                if row:
                    year_str = str(row["ref_year"]) if row["ref_year"] else ""
                    top_ref = f"{row['ref_authors']} {year_str}".strip()
                    top_count = row["cnt"]

            return {
                "open_count": open_count,
                "top_ref": top_ref,
                "top_count": top_count,
            }

    def resolve_gaps(self, entry_lookup: dict) -> int:
        """Auto-resolve open gaps that match entries in library.

        Matches by (author surname, year) or title prefix.
        Returns count of resolved gaps.
        """
        # Build lookup index: "surname year" -> citekey
        lib_index: dict[str, str] = {}
        for citekey, entry in entry_lookup.items():
            if entry.author:
                # Last word of family = actual surname (handles "А. В. Юлин")
                family = entry.author[0].family.lower().strip()
                surname = family.split()[-1] if family else ""
            else:
                surname = ""
            year = str(entry.year) if entry.year else ""
            if surname and year:
                lib_index[f"{surname} {year}"] = citekey
            if entry.title:
                lib_index[entry.title.lower()[:50]] = citekey

        resolved = 0
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT id, ref_authors, ref_year, ref_title "
                "FROM reference_gaps WHERE status='open'"
            )
            for row in cur.fetchall():
                authors = (row["ref_authors"] or "").lower()
                year = str(row["ref_year"]) if row["ref_year"] else ""
                # Extract surname: first word >2 chars (skip initials "А.", "J.")
                words = authors.replace("et al.", "").replace("и др.", "").split()
                surname = ""
                for w in words:
                    clean = w.strip(".,")
                    if len(clean) > 2:
                        surname = clean
                        break
                key = f"{surname} {year}"

                matched_citekey = lib_index.get(key)
                if not matched_citekey and row["ref_title"]:
                    matched_citekey = lib_index.get(row["ref_title"].lower()[:50])

                if matched_citekey:
                    conn.execute(
                        """UPDATE reference_gaps
                           SET status='resolved', resolved_citekey=?,
                               resolved_at=datetime('now')
                           WHERE id=?""",
                        (matched_citekey, row["id"]),
                    )
                    resolved += 1
        return resolved

    # --- Library analysis methods ---

    def get_library_summary(self) -> dict:
        """Comprehensive library summary for AI analysis context."""
        with self._conn() as conn:
            stats = self.get_stats()
            frag_stats = self.get_fragment_stats()
            cov = self.get_coverage_stats()
            gap_summary = self.get_gap_summary()

            # Quality distribution
            cur = conn.execute(
                "SELECT quality_score, COUNT(*) as cnt FROM sources "
                "WHERE status='completed' AND quality_score IS NOT NULL "
                "GROUP BY quality_score ORDER BY quality_score DESC"
            )
            by_quality = {row["quality_score"]: row["cnt"] for row in cur.fetchall()}

            # Average quality
            cur = conn.execute(
                "SELECT AVG(quality_score) as avg_q, AVG(fragment_count) as avg_f "
                "FROM sources WHERE status='completed' AND quality_score > 0"
            )
            row = cur.fetchone()
            avg_quality = round(row["avg_q"], 1) if row["avg_q"] else 0
            avg_fragments = round(row["avg_f"], 1) if row["avg_f"] else 0

            # Sections with zero sources
            zero_sections = [s for s, c in cov["sections"].items() if c == 0] if cov["sections"] else []

            return {
                **stats,
                "fragments_total": frag_stats.get("total", 0),
                "fragments_by_type": frag_stats.get("by_type", {}),
                "chapters": cov.get("chapters", {}),
                "sections": cov.get("sections", {}),
                "by_quality": by_quality,
                "avg_quality": avg_quality,
                "avg_fragments": avg_fragments,
                "zero_sections": zero_sections,
                "ref_gaps_open": gap_summary["open_count"],
                "top_ref_gap": gap_summary.get("top_ref"),
            }

    def get_sources_by_quality(self) -> dict[int, list[dict]]:
        """Completed sources grouped by quality tier (5 → 1)."""
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT id, quality_score, primary_chapter, primary_section,
                          relevance_nr1, relevance_nr2, citation_priority,
                          fragment_count
                   FROM sources WHERE status='completed'
                   ORDER BY quality_score DESC, primary_chapter"""
            )
            result: dict[int, list[dict]] = {}
            for row in cur.fetchall():
                q = row["quality_score"] or 0
                result.setdefault(q, []).append(dict(row))
            return result

    @staticmethod
    def _set_sections_inline(conn, source_id: str, sections: list[str], chapters: list[int]):
        """Write source_sections using an existing connection (avoids nested lock)."""
        conn.execute("DELETE FROM source_sections WHERE source_id=?", (source_id,))
        for sec in sections:
            ch = int(sec.split(".")[0]) if "." in sec else None
            if ch is None:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO source_sections (source_id, chapter, section) VALUES (?, ?, ?)",
                (source_id, ch, sec),
            )
        existing_chapters = {int(s.split(".")[0]) for s in sections if "." in s}
        for ch in chapters:
            if ch not in existing_chapters:
                conn.execute(
                    "INSERT OR IGNORE INTO source_sections (source_id, chapter, section) VALUES (?, ?, ?)",
                    (source_id, ch, str(ch)),
                )

    def sync_source_sections(
        self,
        vault_data: list[dict],
        new_entries: list[tuple[str, dict]],
    ) -> dict:
        """Sync section assignments from vault frontmatter and register new Zotero entries.

        vault_data: [{citekey, primary_section, primary_chapter, sections, chapters,
                      quality, priority, nr1, nr2, note_path}]
        new_entries: [(citekey, {chapter, section, chapters, sections})]

        Returns {"vault_updated": int, "new_registered": int, "unchanged": int}.
        """
        vault_updated = 0
        unchanged = 0
        new_registered = 0

        with self._conn() as conn:
            for vd in vault_data:
                citekey = vd["citekey"]

                cur = conn.execute(
                    """SELECT primary_section, primary_chapter, quality_score,
                              relevance_nr1, relevance_nr2, citation_priority
                       FROM sources WHERE id=?""",
                    (citekey,),
                )
                row = cur.fetchone()
                if not row:
                    # Source in vault but not DB — register it
                    conn.execute("INSERT OR IGNORE INTO sources (id) VALUES (?)", (citekey,))

                # Current DB sections
                cur = conn.execute(
                    "SELECT section FROM source_sections WHERE source_id=?",
                    (citekey,),
                )
                db_sections = {r["section"] for r in cur.fetchall()}
                vault_sections = set(vd.get("sections", []))

                vault_primary = vd.get("primary_section")
                needs_update = (
                    row is None
                    or vault_primary != (row["primary_section"] or None)
                    or vault_sections != db_sections
                    or (vd.get("quality", 0) or 0) != (row["quality_score"] or 0)
                    or (vd.get("nr1", 0) or 0) != (row["relevance_nr1"] or 0)
                    or (vd.get("nr2", 0) or 0) != (row["relevance_nr2"] or 0)
                )

                if needs_update:
                    conn.execute(
                        """UPDATE sources SET
                            primary_section=?, primary_chapter=?,
                            quality_score=?, relevance_nr1=?, relevance_nr2=?,
                            citation_priority=?, note_path=COALESCE(?, note_path),
                            status=CASE WHEN status='pending' THEN 'completed' ELSE status END
                        WHERE id=?""",
                        (
                            vault_primary, vd.get("primary_chapter"),
                            vd.get("quality", 0), vd.get("nr1", 0), vd.get("nr2", 0),
                            vd.get("priority", "medium"), vd.get("note_path"),
                            citekey,
                        ),
                    )
                    self._set_sections_inline(conn, citekey, list(vault_sections), vd.get("chapters", []))
                    # Auto-restore: if user edited vault note, clear prune verdict
                    conn.execute("DELETE FROM prune_verdicts WHERE source_id=?", (citekey,))
                    vault_updated += 1
                else:
                    unchanged += 1

            # Register new Zotero entries not in DB
            for citekey, classification in new_entries:
                cur = conn.execute("SELECT id FROM sources WHERE id=?", (citekey,))
                if cur.fetchone():
                    continue
                conn.execute(
                    """INSERT INTO sources (id, status, primary_chapter, primary_section)
                       VALUES (?, 'pending', ?, ?)""",
                    (citekey, classification.get("chapter"), classification.get("section")),
                )
                sections = classification.get("sections", [])
                chapters = classification.get("chapters", [])
                if sections:
                    self._set_sections_inline(conn, citekey, sections, chapters)
                new_registered += 1

        return {
            "vault_updated": vault_updated,
            "new_registered": new_registered,
            "unchanged": unchanged,
        }

    # ── Discoveries ──────────────────────────────────────────────────────

    def save_discovery(self, section: str, source_type: str, external_id: str,
                       title: str, authors: str, year: Optional[int],
                       abstract: str = "", raw_data: str = "",
                       relevance_score: Optional[int] = None,
                       usage_type: str = "", priority: str = "medium",
                       matched_gap_id: Optional[int] = None):
        """Save a discovered paper."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO discoveries
                   (section, source_type, external_id, title, authors, year,
                    abstract, relevance_score, usage_type, priority,
                    matched_gap_id, raw_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (section, source_type, external_id, title, authors, year,
                 abstract, relevance_score, usage_type, priority,
                 matched_gap_id, raw_data),
            )

    def get_discoveries(self, section: Optional[str] = None,
                        status: str = "pending", limit: int = 50) -> list[dict]:
        """Get discoveries, optionally filtered by section."""
        with self._conn() as conn:
            if section:
                cur = conn.execute(
                    "SELECT * FROM discoveries WHERE section = ? AND status = ? "
                    "ORDER BY relevance_score DESC NULLS LAST, discovered_at DESC LIMIT ?",
                    (section, status, limit),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM discoveries WHERE status = ? "
                    "ORDER BY relevance_score DESC NULLS LAST, discovered_at DESC LIMIT ?",
                    (status, limit),
                )
            return [dict(row) for row in cur.fetchall()]

    def review_discovery(self, discovery_id: int, new_status: str):
        """Update discovery status (accepted/rejected)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE discoveries SET status = ?, reviewed_at = datetime('now') WHERE id = ?",
                (new_status, discovery_id),
            )

    # ── Prune Verdicts ──────────────────────────────────────────────────

    def save_prune_verdicts(self, drop: list[dict], maybe: list[dict]):
        """Replace all prune verdicts with fresh results. Hard-protect valuable sources."""
        with self._conn() as conn:
            conn.execute("DELETE FROM prune_verdicts")
            for item in drop:
                ck = item.get("citekey", "").lstrip("@")
                if not ck or self._is_protected(conn, ck):
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO prune_verdicts (source_id, verdict, reason) VALUES (?, 'drop', ?)",
                    (ck, item.get("reason", "")),
                )
            for item in maybe:
                ck = item.get("citekey", "").lstrip("@")
                if not ck or self._is_protected(conn, ck):
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO prune_verdicts (source_id, verdict, reason) VALUES (?, 'maybe', ?)",
                    (ck, item.get("reason", "")),
                )

    @staticmethod
    def _is_protected(conn, source_id: str) -> bool:
        """Check if source is too valuable to prune."""
        cur = conn.execute(
            "SELECT quality_score, relevance_nr1, relevance_nr2, citation_priority FROM sources WHERE id=?",
            (source_id,),
        )
        row = cur.fetchone()
        if not row:
            return True  # unknown source = don't prune
        return (
            (row["quality_score"] or 0) >= 4
            or (row["relevance_nr1"] or 0) >= 4
            or (row["relevance_nr2"] or 0) >= 4
            or row["citation_priority"] == "high"
        )

    def get_prune_drop_ids(self, max_age_days: int = PRUNE_EXPIRY_DAYS) -> set[str]:
        """Return citekeys with verdict='drop' within expiry window."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT source_id FROM prune_verdicts WHERE verdict='drop' AND updated_at > datetime('now', ?)",
                (f"-{max_age_days} days",),
            )
            return {row["source_id"] for row in cur.fetchall()}

    def get_prune_summary(self) -> dict:
        """Return prune verdict counts for status display."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT verdict, COUNT(*) as cnt FROM prune_verdicts "
                "WHERE updated_at > datetime('now', ?) GROUP BY verdict",
                (f"-{PRUNE_EXPIRY_DAYS} days",),
            )
            result = {"drop": 0, "maybe": 0}
            for row in cur.fetchall():
                result[row["verdict"]] = row["cnt"]
            result["total"] = result["drop"] + result["maybe"]
            return result

    def get_prune_verdicts(self, chapter: Optional[int] = None, verdict: Optional[str] = None) -> list[dict]:
        """Get prune verdicts with source metadata, optionally filtered by chapter/verdict."""
        with self._conn() as conn:
            conditions = [f"pv.updated_at > datetime('now', '-{PRUNE_EXPIRY_DAYS} days')"]
            params: list = []

            if verdict:
                conditions.append("pv.verdict = ?")
                params.append(verdict)

            if chapter is not None:
                ch = str(chapter)
                conditions.append(
                    "EXISTS (SELECT 1 FROM source_sections ss2 "
                    "WHERE ss2.source_id = pv.source_id AND (ss2.section = ? OR ss2.section LIKE ?))"
                )
                params.extend([ch, f"{ch}.%"])

            where = " AND ".join(conditions)
            cur = conn.execute(
                f"""SELECT pv.source_id, pv.verdict, pv.reason,
                           s.quality_score, s.fragment_count,
                           GROUP_CONCAT(DISTINCT ss.section) as sections
                    FROM prune_verdicts pv
                    LEFT JOIN sources s ON s.id = pv.source_id
                    LEFT JOIN source_sections ss ON ss.source_id = pv.source_id
                    WHERE {where}
                    GROUP BY pv.source_id
                    ORDER BY pv.verdict, s.quality_score ASC NULLS LAST""",
                params,
            )
            return [dict(row) for row in cur.fetchall()]

    def clear_prune_verdict(self, source_id: str):
        """Remove prune verdict for a source (e.g. when user edits its vault note)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM prune_verdicts WHERE source_id=?", (source_id,))
