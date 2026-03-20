"""LocalProjectStore — SQLite implementation of the ProjectStore protocol (ADR-014).

Per-project data: section assignments, coverage stats, reference gaps.
Stored at project/.klemma/data/project.db, separate from library.db.

Phase 1C: minimal Protocol implementation. Full migration from monolithic
StateManager (8 repos) to LocalProjectStore happens in Phase 1D.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

_SCHEMA_VERSION = 3

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_sources (
    citekey          TEXT PRIMARY KEY,
    paper_id         TEXT NOT NULL,
    primary_chapter  INTEGER,
    primary_section  TEXT,
    relevance_nr1    INTEGER DEFAULT 0,
    relevance_nr2    INTEGER DEFAULT 0,
    citation_priority TEXT DEFAULT 'medium',
    added_at         TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ps_paper ON project_sources(paper_id);

CREATE TABLE IF NOT EXISTS project_source_sections (
    citekey      TEXT NOT NULL REFERENCES project_sources(citekey) ON DELETE CASCADE,
    section      TEXT NOT NULL,
    section_type TEXT,
    chapter      INTEGER,
    PRIMARY KEY (citekey, section)
);
CREATE INDEX IF NOT EXISTS idx_pss_section ON project_source_sections(section);

CREATE TABLE IF NOT EXISTS project_fragments (
    fragment_id    TEXT NOT NULL PRIMARY KEY,
    citekey        TEXT,
    section        TEXT,
    section_type   TEXT,
    chapter        INTEGER,
    relevance_score INTEGER DEFAULT 3,
    usage_hint     TEXT,
    used_in_draft  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pf_section ON project_fragments(section);
CREATE INDEX IF NOT EXISTS idx_pf_citekey ON project_fragments(citekey);
"""

_MIGRATE_V2 = """
CREATE TABLE IF NOT EXISTS prune_verdicts (
    source_id  TEXT PRIMARY KEY,
    verdict    TEXT NOT NULL CHECK(verdict IN ('drop', 'maybe')),
    reason     TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

_PRUNE_EXPIRY_DAYS = 14


class LocalProjectStore:
    """SQLite-backed ProjectStore at project/.klemma/data/project.db.

    Owns project-specific data: which sources are assigned to which sections,
    per-project fragment relevance, and coverage statistics.

    Content (paper text, embeddings) is NOT stored here — those live in
    library.db via LocalPaperStore.

    Usage::

        store = LocalProjectStore(Path(".klemma/data/project.db"))
        store.set_source_sections("smith2022", "uuid-paper-id", ["1.1", "2.3"], [1])
        stats = store.get_coverage_stats()
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            self._migrate_schema(conn)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        # Always ensure base V1 schema exists (CREATE TABLE IF NOT EXISTS — idempotent).
        # This guards against DBs that somehow have user_version=1 but missing tables.
        conn.executescript(_CREATE_SCHEMA)
        if version < 2:
            conn.executescript(_MIGRATE_V2)
        if version < 3:
            # Multi-user support: scope project data to a specific SaaS user.
            conn.execute(
                "ALTER TABLE project_sources ADD COLUMN user_id TEXT"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ps_user ON project_sources(user_id)"
            )
        if version < _SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    # ------------------------------------------------------------------ #
    # ProjectStore Protocol implementation                                #
    # ------------------------------------------------------------------ #

    def set_source_sections(
        self,
        citekey: str,
        paper_id: str,
        sections: list[str],
        chapters: list[int],
        user_id: Optional[str] = None,
    ) -> None:
        """Upsert project_sources row and replace section assignments."""
        primary_section = sections[0] if sections else None
        primary_chapter = chapters[0] if chapters else None
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO project_sources
                   (citekey, paper_id, primary_chapter, primary_section, user_id)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(citekey) DO UPDATE SET
                       paper_id=excluded.paper_id,
                       primary_chapter=excluded.primary_chapter,
                       primary_section=excluded.primary_section,
                       user_id=COALESCE(excluded.user_id, project_sources.user_id)""",
                (citekey, paper_id, primary_chapter, primary_section, user_id),
            )
            conn.execute(
                "DELETE FROM project_source_sections WHERE citekey = ?", (citekey,)
            )
            conn.executemany(
                """INSERT OR IGNORE INTO project_source_sections
                   (citekey, section, chapter) VALUES (?, ?, ?)""",
                [
                    (citekey, s, chapters[i] if i < len(chapters) else primary_chapter)
                    for i, s in enumerate(sections)
                ],
            )

    def get_coverage_stats(self, user_id: Optional[str] = None) -> dict:
        """Return coverage stats in the same shape as StateManager.get_coverage_stats().

        Keys: total_sources, sections, chapters, by_section (alias),
        section_type_lookup, section_types.
        """
        with self._conn() as conn:
            if user_id is not None:
                total = conn.execute(
                    "SELECT COUNT(*) FROM project_sources WHERE user_id = ?", (user_id,)
                ).fetchone()[0]
                by_section = conn.execute(
                    """SELECT pss.section, COUNT(DISTINCT pss.citekey) as cnt
                       FROM project_source_sections pss
                       JOIN project_sources ps ON ps.citekey = pss.citekey
                       WHERE ps.user_id = ?
                       GROUP BY pss.section ORDER BY pss.section""",
                    (user_id,),
                ).fetchall()
                by_chapter = conn.execute(
                    """SELECT pss.chapter, COUNT(DISTINCT pss.citekey) as cnt
                       FROM project_source_sections pss
                       JOIN project_sources ps ON ps.citekey = pss.citekey
                       WHERE pss.chapter IS NOT NULL AND ps.user_id = ?
                       GROUP BY pss.chapter ORDER BY pss.chapter""",
                    (user_id,),
                ).fetchall()
            else:
                total = conn.execute(
                    "SELECT COUNT(*) FROM project_sources"
                ).fetchone()[0]
                by_section = conn.execute(
                    """SELECT section, COUNT(DISTINCT citekey) as cnt
                       FROM project_source_sections
                       GROUP BY section ORDER BY section"""
                ).fetchall()
                by_chapter = conn.execute(
                    """SELECT chapter, COUNT(DISTINCT citekey) as cnt
                       FROM project_source_sections
                       WHERE chapter IS NOT NULL
                       GROUP BY chapter ORDER BY chapter"""
                ).fetchall()
        sections = {row["section"]: row["cnt"] for row in by_section}
        chapters = {row["chapter"]: row["cnt"] for row in by_chapter}
        return {
            "total_sources": total,
            "sections": sections,
            "by_section": sections,  # backward-compat alias
            "chapters": chapters,
            "section_type_lookup": {},  # section_type_map migration deferred to D2
            "section_types": {},
        }

    def get_reference_gaps(self, **_: object) -> list[dict]:
        """Return reference gaps (Phase 1D: will query monolithic DB via bridge)."""
        # Phase 1C: project.db doesn't own gaps yet — empty until Phase 1D migration
        return []

    # ------------------------------------------------------------------ #
    # Additional helpers                                                  #
    # ------------------------------------------------------------------ #

    def get_source_sections(
        self, citekey: str, user_id: Optional[str] = None
    ) -> list[str]:
        """Return section list for citekey, optionally scoped to a user."""
        with self._conn() as conn:
            if user_id is not None:
                rows = conn.execute(
                    """SELECT pss.section FROM project_source_sections pss
                       JOIN project_sources ps ON ps.citekey = pss.citekey
                       WHERE pss.citekey = ? AND ps.user_id = ?
                       ORDER BY pss.section""",
                    (citekey, user_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT section FROM project_source_sections WHERE citekey=? ORDER BY section",
                    (citekey,),
                ).fetchall()
        return [row["section"] for row in rows]

    def register_fragment(
        self,
        fragment_id: str,
        *,
        citekey: str = "",
        section: str = "",
        section_type: str = "",
        chapter: int = 0,
        relevance_score: int = 3,
    ) -> None:
        """Register a fragment assignment to this project."""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO project_fragments
                   (fragment_id, citekey, section, section_type, chapter, relevance_score)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (fragment_id, citekey, section, section_type, chapter, relevance_score),
            )

    def get_sources_by_section(
        self, section: str, user_id: Optional[str] = None
    ) -> list[str]:
        """Return citekeys assigned to a section, optionally scoped to a user."""
        with self._conn() as conn:
            if user_id is not None:
                rows = conn.execute(
                    """SELECT pss.citekey FROM project_source_sections pss
                       JOIN project_sources ps ON ps.citekey = pss.citekey
                       WHERE pss.section = ? AND ps.user_id = ?
                       ORDER BY pss.citekey""",
                    (section, user_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT citekey FROM project_source_sections WHERE section=? ORDER BY citekey",
                    (section,),
                ).fetchall()
        return [row["citekey"] for row in rows]

    def count_sources(self, user_id: Optional[str] = None) -> int:
        with self._conn() as conn:
            if user_id is not None:
                return conn.execute(
                    "SELECT COUNT(*) FROM project_sources WHERE user_id = ?", (user_id,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM project_sources").fetchone()[0]

    # ------------------------------------------------------------------ #
    # Prune verdicts (schema v2)                                          #
    # ------------------------------------------------------------------ #

    def save_prune_verdicts(self, drop: list[dict], maybe: list[dict]) -> None:
        """Replace all prune verdicts with fresh results."""
        with self._conn() as conn:
            conn.execute("DELETE FROM prune_verdicts")
            for item in drop:
                ck = item.get("citekey", "").lstrip("@")
                if not ck:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO prune_verdicts (source_id, verdict, reason)"
                    " VALUES (?, 'drop', ?)",
                    (ck, item.get("reason", "")),
                )
            for item in maybe:
                ck = item.get("citekey", "").lstrip("@")
                if not ck:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO prune_verdicts (source_id, verdict, reason)"
                    " VALUES (?, 'maybe', ?)",
                    (ck, item.get("reason", "")),
                )

    def get_prune_verdicts(
        self,
        verdict: str | None = None,
        chapter: int | None = None,
        section_type: str | None = None,
    ) -> list[dict]:
        """Return prune verdicts, optionally filtered by verdict type."""
        with self._conn() as conn:
            conditions = [
                f"pv.updated_at > datetime('now', '-{_PRUNE_EXPIRY_DAYS} days')"
            ]
            params: list = []

            if verdict:
                conditions.append("pv.verdict = ?")
                params.append(verdict)

            if chapter is not None:
                ch = str(chapter)
                conditions.append(
                    "EXISTS (SELECT 1 FROM project_source_sections pss"
                    " WHERE pss.citekey = pv.source_id"
                    " AND (pss.section = ? OR pss.section LIKE ?))"
                )
                params.extend([ch, f"{ch}.%"])

            if section_type:
                conditions.append(
                    "EXISTS (SELECT 1 FROM project_source_sections pss2"
                    " WHERE pss2.citekey = pv.source_id AND pss2.section_type = ?)"
                )
                params.append(section_type)

            where = " AND ".join(conditions)
            cur = conn.execute(
                f"SELECT pv.source_id, pv.verdict, pv.reason,"
                f" GROUP_CONCAT(DISTINCT pss3.section) as sections"
                f" FROM prune_verdicts pv"
                f" LEFT JOIN project_source_sections pss3 ON pss3.citekey = pv.source_id"
                f" WHERE {where}"
                f" GROUP BY pv.source_id"
                f" ORDER BY pv.verdict, pv.source_id",
                params,
            )
            return [dict(row) for row in cur.fetchall()]

    def get_prune_drop_ids(self, max_age_days: int = _PRUNE_EXPIRY_DAYS) -> set[str]:
        """Return citekeys with verdict='drop' within expiry window."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT source_id FROM prune_verdicts"
                " WHERE verdict='drop' AND updated_at > datetime('now', ?)",
                (f"-{max_age_days} days",),
            )
            return {row["source_id"] for row in cur.fetchall()}

    def get_prune_summary(self) -> dict:
        """Return prune verdict counts."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT verdict, COUNT(*) as cnt FROM prune_verdicts"
                " WHERE updated_at > datetime('now', ?) GROUP BY verdict",
                (f"-{_PRUNE_EXPIRY_DAYS} days",),
            )
            result = {"drop": 0, "maybe": 0}
            for row in cur.fetchall():
                result[row["verdict"]] = row["cnt"]
            result["total"] = result["drop"] + result["maybe"]
            return result

    def clear_prune_verdict(self, source_id: str) -> None:
        """Remove prune verdict for a source."""
        with self._conn() as conn:
            conn.execute("DELETE FROM prune_verdicts WHERE source_id=?", (source_id,))
