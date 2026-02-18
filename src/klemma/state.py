"""Unified SQLite state manager."""

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
"""


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

    def get_pending_sources(self, limit: int = 10) -> list[str]:
        today = date.today().isoformat()
        with self._conn() as conn:
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

    def get_by_chapter(self, chapter: int) -> list[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT DISTINCT s.id, s.note_path, s.quality_score, s.primary_section,
                          s.relevance_nr1, s.relevance_nr2, s.citation_priority,
                          s.fragment_count
                   FROM sources s
                   LEFT JOIN source_sections ss ON s.id = ss.source_id
                   WHERE s.status=? AND (s.primary_chapter=? OR ss.chapter=?)
                   ORDER BY s.citation_priority DESC, s.quality_score DESC""",
                (ProcessingStatus.COMPLETED.value, chapter, chapter),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_by_section(self, section: str) -> list[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT DISTINCT s.id, s.note_path, s.quality_score, s.primary_chapter,
                          s.relevance_nr1, s.relevance_nr2, s.citation_priority,
                          s.fragment_count
                   FROM sources s
                   LEFT JOIN source_sections ss ON s.id = ss.source_id
                   WHERE s.status=? AND (s.primary_section LIKE ? OR ss.section LIKE ?)
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
                """SELECT rq.*, s.id as citekey
                   FROM reading_queue rq
                   JOIN sources s ON rq.source_id = s.id
                   WHERE rq.status = 'queued'
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
