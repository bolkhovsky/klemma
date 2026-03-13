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
from typing import Generator

_SCHEMA_VERSION = 1

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
        if version < _SCHEMA_VERSION:
            conn.executescript(_CREATE_SCHEMA)
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
    ) -> None:
        """Upsert project_sources row and replace section assignments."""
        primary_section = sections[0] if sections else None
        primary_chapter = chapters[0] if chapters else None
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO project_sources
                   (citekey, paper_id, primary_chapter, primary_section)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(citekey) DO UPDATE SET
                       paper_id=excluded.paper_id,
                       primary_chapter=excluded.primary_chapter,
                       primary_section=excluded.primary_section""",
                (citekey, paper_id, primary_chapter, primary_section),
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

    def get_coverage_stats(self) -> dict:
        """Return coverage stats in the same shape as StateManager.get_coverage_stats().

        Keys: total_sources, sections, chapters, by_section (alias),
        section_type_lookup, section_types.
        """
        with self._conn() as conn:
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

    def get_source_sections(self, citekey: str) -> list[str]:
        """Return section list for citekey."""
        with self._conn() as conn:
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

    def get_sources_by_section(self, section: str) -> list[str]:
        """Return citekeys assigned to a section."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT citekey FROM project_source_sections WHERE section=? ORDER BY citekey",
                (section,),
            ).fetchall()
        return [row["citekey"] for row in rows]

    def count_sources(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM project_sources").fetchone()[0]
