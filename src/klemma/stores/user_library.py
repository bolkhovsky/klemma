"""LocalUserLibrary — SQLite implementation of the UserLibrary protocol (ADR-014).

Adds user_sources tables to ~/.klemma/library.db (same file as LocalPaperStore).
Maps citekey → paper_id for the User Library tier.

Multi-user support (v4): user_id column added to user_sources for SaaS data
isolation. CLI mode passes user_id=None to skip filtering (single-user on disk).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator, Optional

if TYPE_CHECKING:
    from ..models import UserSource

_SCHEMA_VERSION = 5  # v5: composite PK (user_id, citekey) for multi-user isolation

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_sources (
    citekey     TEXT NOT NULL,
    paper_id    TEXT NOT NULL,
    status      TEXT DEFAULT 'pending',
    pdf_path    TEXT,
    note_path   TEXT,
    quality_score INTEGER,
    added_at    TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now')),
    project_id  TEXT,
    user_id     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, citekey)
);
CREATE INDEX IF NOT EXISTS idx_user_sources_paper ON user_sources(paper_id);
CREATE INDEX IF NOT EXISTS idx_user_sources_user ON user_sources(user_id);

CREATE TABLE IF NOT EXISTS user_source_chapters (
    citekey TEXT NOT NULL,
    chapter INTEGER NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, citekey, chapter)
);

CREATE TABLE IF NOT EXISTS user_source_sections (
    citekey TEXT NOT NULL,
    section TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, citekey, section)
);
"""


class LocalUserLibrary:
    """SQLite-backed UserLibrary at ~/.klemma/library.db.

    Shares the same database file as LocalPaperStore. Adds user_sources,
    user_source_chapters, user_source_sections tables.

    Multi-user mode (SaaS): pass user_id to all read/write methods to scope
    operations to a single user.
    Single-user mode (CLI): pass user_id=None — no filtering applied.

    Usage::

        lib = LocalUserLibrary(Path.home() / ".klemma" / "library.db")
        # CLI (single-user)
        lib.add_source("abc123uuid", "smith2022nlp", status="completed")
        # SaaS (multi-user)
        lib.add_source("abc123uuid", "smith2022nlp", user_id="user-uuid", status="completed")
        paper_id = lib.resolve_paper_id("smith2022nlp", user_id="user-uuid")
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
        if version < 2:
            conn.executescript(_CREATE_SCHEMA)
        if version < 3:
            try:
                conn.execute(
                    "ALTER TABLE user_sources ADD COLUMN project_id TEXT"
                )
            except sqlite3.OperationalError:
                pass  # column already exists
        if version < 4:
            # Multi-user support: scope all sources to a specific user.
            # NULL = legacy CLI sources (owned by no particular SaaS user).
            try:
                conn.execute(
                    "ALTER TABLE user_sources ADD COLUMN user_id TEXT"
                )
            except sqlite3.OperationalError:
                pass  # column already exists
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_sources_user ON user_sources(user_id)"
            )
        # v5: composite PK (user_id, citekey) to prevent cross-user collision.
        # Check actual PK structure (not just version) for idempotency — version
        # may have been bumped without the migration running (e.g. interrupted).
        pk_cols = [
            r[1] for r in conn.execute("PRAGMA table_info(user_sources)").fetchall()
            if r[5] > 0
        ]
        needs_pk_migration = pk_cols == ["citekey"]  # old schema: citekey-only PK
        if version < 5 or needs_pk_migration:
            # NULL user_id → '' (empty string) for PK compatibility.
            # SQLite doesn't support ALTER PRIMARY KEY — recreate tables.
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS user_sources_v5 (
                    citekey     TEXT NOT NULL,
                    paper_id    TEXT NOT NULL,
                    status      TEXT DEFAULT 'pending',
                    pdf_path    TEXT,
                    note_path   TEXT,
                    quality_score INTEGER,
                    added_at    TEXT DEFAULT (datetime('now')),
                    updated_at  TEXT DEFAULT (datetime('now')),
                    project_id  TEXT,
                    user_id     TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (user_id, citekey)
                );
                INSERT OR IGNORE INTO user_sources_v5
                    (citekey, paper_id, status, pdf_path, note_path,
                     quality_score, added_at, updated_at, project_id, user_id)
                SELECT citekey, paper_id, status, pdf_path, note_path,
                       quality_score, added_at, updated_at, project_id,
                       COALESCE(user_id, '')
                FROM user_sources;
                DROP TABLE IF EXISTS user_source_chapters;
                DROP TABLE IF EXISTS user_source_sections;
                DROP TABLE user_sources;
                ALTER TABLE user_sources_v5 RENAME TO user_sources;
                CREATE INDEX IF NOT EXISTS idx_user_sources_paper ON user_sources(paper_id);
                CREATE INDEX IF NOT EXISTS idx_user_sources_user ON user_sources(user_id);
                CREATE TABLE IF NOT EXISTS user_source_chapters (
                    citekey TEXT NOT NULL,
                    chapter INTEGER NOT NULL,
                    user_id TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (user_id, citekey, chapter)
                );
                CREATE TABLE IF NOT EXISTS user_source_sections (
                    citekey TEXT NOT NULL,
                    section TEXT NOT NULL,
                    user_id TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (user_id, citekey, section)
                );
            """)
        if version < _SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    @staticmethod
    def _uid(user_id: Optional[str]) -> str:
        """Normalize user_id: None → '' for composite PK compatibility."""
        return user_id if user_id is not None else ""

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
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
        **_: object,
    ) -> None:
        """Register citekey → paper_id mapping. Upserts on citekey conflict.

        In SaaS mode, pass user_id to scope the source to a specific user.
        Two different users may register the same citekey (they point to the
        same global paper but are independent library entries).
        """
        uid = self._uid(user_id)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO user_sources
                   (citekey, paper_id, status, pdf_path, note_path, quality_score,
                    project_id, user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, citekey) DO UPDATE SET
                       paper_id=excluded.paper_id,
                       status=excluded.status,
                       pdf_path=excluded.pdf_path,
                       note_path=excluded.note_path,
                       quality_score=excluded.quality_score,
                       project_id=COALESCE(excluded.project_id, user_sources.project_id),
                       updated_at=datetime('now')""",
                (citekey, paper_id, status, pdf_path, note_path, quality_score,
                 project_id, uid),
            )
            if chapters:
                conn.execute(
                    "DELETE FROM user_source_chapters WHERE citekey = ? AND user_id = ?",
                    (citekey, uid),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO user_source_chapters (citekey, chapter, user_id) VALUES (?,?,?)",
                    [(citekey, ch, uid) for ch in chapters],
                )
            if sections:
                conn.execute(
                    "DELETE FROM user_source_sections WHERE citekey = ? AND user_id = ?",
                    (citekey, uid),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO user_source_sections (citekey, section, user_id) VALUES (?,?,?)",
                    [(citekey, s, uid) for s in sections],
                )

    def get_source_by_citekey(
        self, citekey: str, user_id: Optional[str] = None
    ) -> Optional["UserSource"]:
        """Return UserSource for citekey, or None if not registered.

        In SaaS mode, pass user_id to scope lookup to a specific user.
        """
        from ..models import UserSource

        with self._conn() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT * FROM user_sources WHERE citekey = ? AND user_id = ?",
                    (citekey, user_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM user_sources WHERE citekey = ?", (citekey,)
                ).fetchone()
            if not row:
                return None
            row_uid = row["user_id"] or ""
            chapters = [
                r[0]
                for r in conn.execute(
                    "SELECT chapter FROM user_source_chapters WHERE citekey = ? AND user_id = ? ORDER BY chapter",
                    (citekey, row_uid),
                ).fetchall()
            ]
            sections = [
                r[0]
                for r in conn.execute(
                    "SELECT section FROM user_source_sections WHERE citekey = ? AND user_id = ? ORDER BY section",
                    (citekey, row_uid),
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

    def resolve_paper_id(
        self, citekey: str, user_id: Optional[str] = None
    ) -> Optional[str]:
        """Return paper_id for citekey, or None if not registered."""
        with self._conn() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT paper_id FROM user_sources WHERE citekey = ? AND user_id = ?",
                    (citekey, user_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT paper_id FROM user_sources WHERE citekey = ?", (citekey,)
                ).fetchone()
        return row["paper_id"] if row else None

    def get_existing_citekeys(self, user_id: Optional[str] = None) -> set[str]:
        """Return all registered citekeys, optionally scoped to a user."""
        with self._conn() as conn:
            if user_id is not None:
                rows = conn.execute(
                    "SELECT citekey FROM user_sources WHERE user_id = ?", (user_id,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT citekey FROM user_sources").fetchall()
        return {row["citekey"] for row in rows}

    def get_source_by_paper_id(
        self, paper_id: str, user_id: Optional[str] = None
    ) -> Optional["UserSource"]:
        """Return the first UserSource pointing to paper_id, or None.

        Used to detect when the same PDF is re-uploaded (same paper_id).
        Scoped to user_id in SaaS mode to avoid cross-user leakage.
        """
        from ..models import UserSource

        with self._conn() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT * FROM user_sources WHERE paper_id = ? AND user_id = ? LIMIT 1",
                    (paper_id, user_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM user_sources WHERE paper_id = ? LIMIT 1",
                    (paper_id,),
                ).fetchone()
            if not row:
                return None
            citekey = row["citekey"]
            _uid = row["user_id"]
            row_uid = row["user_id"] or ""
            chapters = [
                r[0]
                for r in conn.execute(
                    "SELECT chapter FROM user_source_chapters WHERE citekey = ? AND user_id = ? ORDER BY chapter",
                    (citekey, row_uid),
                ).fetchall()
            ]
            sections = [
                r[0]
                for r in conn.execute(
                    "SELECT section FROM user_source_sections WHERE citekey = ? AND user_id = ? ORDER BY section",
                    (citekey, row_uid),
                ).fetchall()
            ]
        return UserSource(
            citekey=citekey,
            paper_id=row["paper_id"],
            status=row["status"] or "pending",
            pdf_path=row["pdf_path"],
            note_path=row["note_path"],
            quality_score=row["quality_score"],
            chapters=chapters,
            sections=sections,
        )

    # ------------------------------------------------------------------ #
    # Additional helpers (beyond Protocol minimum)                        #
    # ------------------------------------------------------------------ #

    def remove_source(self, citekey: str, user_id: Optional[str] = None) -> bool:
        """Remove a source from the user's library. Returns True if existed.

        In SaaS mode, pass user_id to prevent cross-user deletion.
        """
        with self._conn() as conn:
            if user_id is not None:
                # Only delete if the source belongs to this user
                row = conn.execute(
                    "SELECT citekey FROM user_sources WHERE citekey = ? AND user_id = ?",
                    (citekey, user_id),
                ).fetchone()
                if not row:
                    return False
                conn.execute(
                    "DELETE FROM user_source_chapters WHERE citekey = ? AND user_id = ?",
                    (citekey, user_id),
                )
                conn.execute(
                    "DELETE FROM user_source_sections WHERE citekey = ? AND user_id = ?",
                    (citekey, user_id),
                )
                cursor = conn.execute(
                    "DELETE FROM user_sources WHERE citekey = ? AND user_id = ?",
                    (citekey, user_id),
                )
            else:
                conn.execute("DELETE FROM user_source_chapters WHERE citekey = ?", (citekey,))
                conn.execute("DELETE FROM user_source_sections WHERE citekey = ?", (citekey,))
                cursor = conn.execute("DELETE FROM user_sources WHERE citekey = ?", (citekey,))
        return cursor.rowcount > 0

    def update_status(
        self, citekey: str, status: str, user_id: Optional[str] = None
    ) -> None:
        """Update processing status for citekey."""
        with self._conn() as conn:
            if user_id is not None:
                conn.execute(
                    "UPDATE user_sources SET status=?, updated_at=datetime('now')"
                    " WHERE citekey=? AND user_id=?",
                    (status, citekey, user_id),
                )
            else:
                conn.execute(
                    "UPDATE user_sources SET status=?, updated_at=datetime('now')"
                    " WHERE citekey=?",
                    (status, citekey),
                )

    def get_all_sources(
        self,
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list["UserSource"]:
        """Return all UserSource entries, optionally filtered by project, user, and time.

        Args:
            project_id: Restrict to sources belonging to this project.
            user_id: Restrict to sources belonging to this user.
            since: ISO 8601 timestamp. Only return sources added on or after this time.
                   Used for incremental pull (#260 item 7).
        """
        with self._conn() as conn:
            conditions = []
            params: list = []

            if user_id is not None:
                conditions.append("user_id = ?")
                params.append(user_id)

            if project_id is not None:
                conditions.append("(project_id = ? OR project_id IS NULL)")
                params.append(project_id)

            if since is not None:
                conditions.append("added_at >= ?")
                params.append(since)

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = conn.execute(
                f"SELECT citekey, user_id FROM user_sources {where} ORDER BY added_at",
                params,
            ).fetchall()
        return [  # type: ignore[return-value]
            self.get_source_by_citekey(
                row["citekey"],
                user_id=row["user_id"] if row["user_id"] else None,
            )
            for row in rows
        ]

    def count(self, user_id: Optional[str] = None) -> int:
        """Return total number of registered sources, optionally scoped to a user."""
        with self._conn() as conn:
            if user_id is not None:
                return conn.execute(
                    "SELECT COUNT(*) FROM user_sources WHERE user_id = ?", (user_id,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM user_sources").fetchone()[0]
