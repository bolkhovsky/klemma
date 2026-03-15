"""LocalUserLibrary — SQLite implementation of the UserLibrary protocol (ADR-014).

Adds user_sources tables to ~/.klemma/library.db (same file as LocalPaperStore).
Maps citekey → paper_id for the User Library tier.

Phase 1C: single-user mode (no user_id column). SaaS sprint will add user_id.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator, Optional

if TYPE_CHECKING:
    from ..models import UserSource

_SCHEMA_VERSION = 2  # library.db version after adding user_sources

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_sources (
    citekey     TEXT PRIMARY KEY,
    paper_id    TEXT NOT NULL,
    status      TEXT DEFAULT 'pending',
    pdf_path    TEXT,
    note_path   TEXT,
    quality_score INTEGER,
    added_at    TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_user_sources_paper ON user_sources(paper_id);

CREATE TABLE IF NOT EXISTS user_source_chapters (
    citekey TEXT NOT NULL REFERENCES user_sources(citekey) ON DELETE CASCADE,
    chapter INTEGER NOT NULL,
    PRIMARY KEY (citekey, chapter)
);

CREATE TABLE IF NOT EXISTS user_source_sections (
    citekey TEXT NOT NULL REFERENCES user_sources(citekey) ON DELETE CASCADE,
    section TEXT NOT NULL,
    PRIMARY KEY (citekey, section)
);
"""


class LocalUserLibrary:
    """SQLite-backed UserLibrary at ~/.klemma/library.db.

    Shares the same database file as LocalPaperStore. Adds user_sources,
    user_source_chapters, user_source_sections tables (schema version 2).

    Usage::

        lib = LocalUserLibrary(Path.home() / ".klemma" / "library.db")
        lib.add_source("abc123uuid", "smith2022nlp", status="completed")
        paper_id = lib.resolve_paper_id("smith2022nlp")
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
    # UserLibrary Protocol implementation                                 #
    # ------------------------------------------------------------------ #

    def add_source(
        self,
        paper_id: str,
        citekey: str,
        *,
        status: str = "pending",
        pdf_path: Optional[str] = None,
        note_path: Optional[str] = None,
        quality_score: Optional[int] = None,
        chapters: Optional[list[int]] = None,
        sections: Optional[list[str]] = None,
        **_: object,
    ) -> None:
        """Register citekey → paper_id mapping. Upserts on citekey conflict."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO user_sources
                   (citekey, paper_id, status, pdf_path, note_path, quality_score)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(citekey) DO UPDATE SET
                       paper_id=excluded.paper_id,
                       status=excluded.status,
                       pdf_path=excluded.pdf_path,
                       note_path=excluded.note_path,
                       quality_score=excluded.quality_score,
                       updated_at=datetime('now')""",
                (citekey, paper_id, status, pdf_path, note_path, quality_score),
            )
            if chapters:
                conn.execute(
                    "DELETE FROM user_source_chapters WHERE citekey = ?", (citekey,)
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO user_source_chapters (citekey, chapter) VALUES (?,?)",
                    [(citekey, ch) for ch in chapters],
                )
            if sections:
                conn.execute(
                    "DELETE FROM user_source_sections WHERE citekey = ?", (citekey,)
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO user_source_sections (citekey, section) VALUES (?,?)",
                    [(citekey, s) for s in sections],
                )

    def get_source_by_citekey(self, citekey: str) -> Optional["UserSource"]:
        """Return UserSource for citekey, or None if not registered."""
        from ..models import UserSource

        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_sources WHERE citekey = ?", (citekey,)
            ).fetchone()
            if not row:
                return None
            chapters = [
                r[0]
                for r in conn.execute(
                    "SELECT chapter FROM user_source_chapters WHERE citekey = ? ORDER BY chapter",
                    (citekey,),
                ).fetchall()
            ]
            sections = [
                r[0]
                for r in conn.execute(
                    "SELECT section FROM user_source_sections WHERE citekey = ? ORDER BY section",
                    (citekey,),
                ).fetchall()
            ]
        return UserSource(
            citekey=row["citekey"],
            paper_id=row["paper_id"],
            status=row["status"] or "pending",
            pdf_path=row["pdf_path"],
            note_path=row["note_path"],
            quality_score=row["quality_score"],
            chapters=chapters,
            sections=sections,
        )

    def resolve_paper_id(self, citekey: str) -> Optional[str]:
        """Return paper_id for citekey, or None if not registered."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT paper_id FROM user_sources WHERE citekey = ?", (citekey,)
            ).fetchone()
        return row["paper_id"] if row else None

    def get_existing_citekeys(self) -> set[str]:
        """Return all registered citekeys."""
        with self._conn() as conn:
            rows = conn.execute("SELECT citekey FROM user_sources").fetchall()
        return {row["citekey"] for row in rows}

    # ------------------------------------------------------------------ #
    # Additional helpers (beyond Protocol minimum)                        #
    # ------------------------------------------------------------------ #

    def remove_source(self, citekey: str) -> bool:
        """Remove a source from the user's library. Returns True if existed."""
        with self._conn() as conn:
            conn.execute("DELETE FROM user_source_chapters WHERE citekey = ?", (citekey,))
            conn.execute("DELETE FROM user_source_sections WHERE citekey = ?", (citekey,))
            cursor = conn.execute("DELETE FROM user_sources WHERE citekey = ?", (citekey,))
        return cursor.rowcount > 0

    def update_status(self, citekey: str, status: str) -> None:
        """Update processing status for citekey."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE user_sources SET status=?, updated_at=datetime('now') WHERE citekey=?",
                (status, citekey),
            )

    def get_all_sources(self) -> list["UserSource"]:
        """Return all UserSource entries."""

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT citekey FROM user_sources ORDER BY added_at"
            ).fetchall()
        return [self.get_source_by_citekey(row["citekey"]) for row in rows]  # type: ignore[return-value]

    def count(self) -> int:
        """Return total number of registered sources."""
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM user_sources").fetchone()[0]
