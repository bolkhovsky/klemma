"""LocalUserLibrary — SQLite implementation of the UserLibrary protocol (ADR-014).

Adds user_sources tables to ~/.klemma/library.db (same file as LocalPaperStore).
Maps citekey → paper_id for the User Library tier.

Multi-user support (v4): user_id column added to user_sources for SaaS data
isolation. Project membership is many-to-many via user_source_projects (v7).
CLI mode passes user_id=None to skip filtering (single-user on disk).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator, Optional

if TYPE_CHECKING:
    from ..models import UserSource

_SCHEMA_VERSION = 7  # v7: many-to-many source ↔ project links

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

CREATE TABLE IF NOT EXISTS user_source_projects (
    citekey    TEXT NOT NULL,
    project_id TEXT NOT NULL,
    user_id    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, citekey, project_id)
);
CREATE INDEX IF NOT EXISTS idx_usp_project_user ON user_source_projects(project_id, user_id);
CREATE INDEX IF NOT EXISTS idx_usp_citekey_user ON user_source_projects(citekey, user_id);
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
        if version < 6:
            # v6: external_citekey — optional BBT-imported display label.
            # When set, the cloud emits this (not citekey) into generated text
            # and echoes it in API responses, so [@key] references in drafts
            # match the user's local .bib file. citekey itself stays immutable
            # (stability invariant, issue #268).
            cols = {r[1] for r in conn.execute("PRAGMA table_info(user_sources)").fetchall()}
            if "external_citekey" not in cols:
                conn.execute("ALTER TABLE user_sources ADD COLUMN external_citekey TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_sources_external_ck"
                " ON user_sources(user_id, external_citekey)"
            )
        has_project_links = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_source_projects'"
        ).fetchone()
        if version < 7 or not has_project_links:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS user_source_projects (
                    citekey    TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    user_id    TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (user_id, citekey, project_id)
                );
                CREATE INDEX IF NOT EXISTS idx_usp_project_user
                    ON user_source_projects(project_id, user_id);
                CREATE INDEX IF NOT EXISTS idx_usp_citekey_user
                    ON user_source_projects(citekey, user_id);
                INSERT OR IGNORE INTO user_source_projects
                    (citekey, project_id, user_id)
                SELECT citekey, project_id, COALESCE(user_id, '')
                FROM user_sources
                WHERE project_id IS NOT NULL AND TRIM(project_id) <> '';
            """)
        if version < _SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    @staticmethod
    def _uid(user_id: Optional[str]) -> str:
        """Normalize user_id: None → '' for composite PK compatibility."""
        return user_id if user_id is not None else ""

    @staticmethod
    def _project(project_id: Optional[str]) -> Optional[str]:
        """Normalize project_id: empty strings become None."""
        if project_id is None:
            return None
        project_id = project_id.strip()
        return project_id or None

    def _attach_source_to_project(
        self,
        conn: sqlite3.Connection,
        citekey: str,
        project_id: Optional[str],
        user_id: Optional[str],
    ) -> None:
        """Attach a source to a project without affecting source metadata."""
        project_id = self._project(project_id)
        if project_id is None:
            return
        conn.execute(
            """INSERT OR IGNORE INTO user_source_projects
               (citekey, project_id, user_id) VALUES (?, ?, ?)""",
            (citekey, project_id, self._uid(user_id)),
        )

    def _row_to_source(
        self, conn: sqlite3.Connection, row: sqlite3.Row
    ) -> "UserSource":
        """Hydrate a UserSource from a user_sources row."""
        from ..models import UserSource

        citekey = row["citekey"]
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
        project_ids = [
            r[0]
            for r in conn.execute(
                "SELECT project_id FROM user_source_projects WHERE citekey = ? AND user_id = ? ORDER BY project_id",
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
            external_citekey=_safe_ext_ck(row),
            project_id=_safe_col(row, "project_id"),
            project_ids=project_ids,
        )

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
        project_id = self._project(project_id)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO user_sources
                   (citekey, paper_id, status, pdf_path, note_path, quality_score,
                    project_id, user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, citekey) DO UPDATE SET
                       paper_id=excluded.paper_id,
                       status=excluded.status,
                       pdf_path=COALESCE(excluded.pdf_path, user_sources.pdf_path),
                       note_path=COALESCE(excluded.note_path, user_sources.note_path),
                       quality_score=COALESCE(excluded.quality_score, user_sources.quality_score),
                       project_id=COALESCE(user_sources.project_id, excluded.project_id),
                       updated_at=datetime('now')""",
                (citekey, paper_id, status, pdf_path, note_path, quality_score,
                 project_id, uid),
            )
            self._attach_source_to_project(conn, citekey, project_id, user_id)
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
            return self._row_to_source(conn, row)

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
            return self._row_to_source(conn, row)

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
                conn.execute(
                    "DELETE FROM user_source_projects WHERE citekey = ? AND user_id = ?",
                    (citekey, user_id),
                )
                cursor = conn.execute(
                    "DELETE FROM user_sources WHERE citekey = ? AND user_id = ?",
                    (citekey, user_id),
                )
            else:
                conn.execute("DELETE FROM user_source_chapters WHERE citekey = ?", (citekey,))
                conn.execute("DELETE FROM user_source_sections WHERE citekey = ?", (citekey,))
                conn.execute("DELETE FROM user_source_projects WHERE citekey = ?", (citekey,))
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

    def get_project_citekeys(
        self, project_id: str, user_id: Optional[str] = None
    ) -> set[str]:
        """Return citekeys strictly attached to a project (excludes unassigned)."""
        with self._conn() as conn:
            if user_id is not None:
                rows = conn.execute(
                    "SELECT citekey FROM user_source_projects WHERE project_id = ? AND user_id = ?",
                    (project_id, user_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT citekey FROM user_source_projects WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
        return {r["citekey"] for r in rows}

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
                conditions.append("us.user_id = ?")
                params.append(user_id)

            if project_id is not None:
                conditions.append(
                    """(
                        EXISTS (
                            SELECT 1
                            FROM user_source_projects usp
                            WHERE usp.citekey = us.citekey
                              AND usp.user_id = us.user_id
                              AND usp.project_id = ?
                        )
                        OR NOT EXISTS (
                            SELECT 1
                            FROM user_source_projects usp_any
                            WHERE usp_any.citekey = us.citekey
                              AND usp_any.user_id = us.user_id
                        )
                    )"""
                )
                params.append(project_id)

            if since is not None:
                conditions.append("us.added_at >= ?")
                params.append(since)

            where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = conn.execute(
                f"SELECT us.citekey, us.user_id FROM user_sources us {where} ORDER BY us.added_at",
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

    def get_citekey_map(
        self, paper_ids: list[str], user_id: Optional[str] = None
    ) -> dict[str, str]:
        """Return {paper_id: citekey} for the given paper_ids scoped to a user.

        Used by list_reference_gaps to resolve paper_id → citekey for section
        lookups without N+1 queries.
        """
        if not paper_ids:
            return {}
        uid = self._uid(user_id)
        placeholders = ",".join("?" for _ in paper_ids)
        with self._conn() as conn:
            if user_id is not None:
                rows = conn.execute(
                    f"SELECT paper_id, citekey FROM user_sources"
                    f" WHERE paper_id IN ({placeholders}) AND user_id = ?",
                    (*paper_ids, uid),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT paper_id, citekey FROM user_sources"
                    f" WHERE paper_id IN ({placeholders})",
                    paper_ids,
                ).fetchall()
        return {row["paper_id"]: row["citekey"] for row in rows}

    # ------------------------------------------------------------------ #
    # External citekey (BBT import) support                               #
    # ------------------------------------------------------------------ #

    def set_external_citekey(
        self,
        citekey: str,
        external_citekey: Optional[str],
        user_id: Optional[str] = None,
    ) -> bool:
        """Set or clear the BBT-imported display override for a source.

        ``citekey`` is the internal immutable key used as PK. Pass
        ``external_citekey=None`` to clear a previous import. Returns True
        if the row existed and was updated.
        """
        uid = self._uid(user_id)
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE user_sources SET external_citekey = ?, updated_at = datetime('now')"
                " WHERE citekey = ? AND user_id = ?",
                (external_citekey, citekey, uid),
            )
        return cursor.rowcount > 0

    def get_source_by_any_key(
        self, key: str, user_id: Optional[str] = None
    ) -> Optional["UserSource"]:
        """Resolve a user-submitted key to a UserSource by either citekey
        or external_citekey.

        Order of resolution:
            1. exact citekey match (immutable, stability invariant)
            2. external_citekey match (BBT display override)

        Returns ``None`` if neither column matches. Used by read-path routes
        that accept a user-submitted key in URL or query. All write paths
        must use the returned ``source.citekey`` (internal).
        """
        # Step 1: try citekey (common case — internal or already-migrated draft)
        src = self.get_source_by_citekey(key, user_id=user_id)
        if src is not None:
            return src
        # Step 2: fall back to external_citekey
        uid = self._uid(user_id)
        with self._conn() as conn:
            if user_id is not None:
                row = conn.execute(
                    "SELECT citekey FROM user_sources"
                    " WHERE external_citekey = ? AND user_id = ?",
                    (key, uid),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT citekey FROM user_sources WHERE external_citekey = ?",
                    (key,),
                ).fetchone()
        if not row:
            return None
        # Delegate to primary lookup so chapters/sections are loaded consistently
        return self.get_source_by_citekey(row["citekey"], user_id=user_id)

    def get_display_citekeys(
        self, citekeys: list[str], user_id: Optional[str] = None
    ) -> dict[str, str]:
        """Batch-resolve {internal citekey → display citekey}.

        Display = ``external_citekey`` if set, else the internal citekey
        itself. Used by worker tasks (generate_sentences, generate_draft)
        and by routes that echo display keys in list responses. Citekeys
        not found in the library are absent from the returned dict.
        """
        if not citekeys:
            return {}
        uid = self._uid(user_id)
        placeholders = ",".join("?" for _ in citekeys)
        with self._conn() as conn:
            if user_id is not None:
                rows = conn.execute(
                    f"SELECT citekey, external_citekey FROM user_sources"
                    f" WHERE citekey IN ({placeholders}) AND user_id = ?",
                    (*citekeys, uid),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT citekey, external_citekey FROM user_sources"
                    f" WHERE citekey IN ({placeholders})",
                    citekeys,
                ).fetchall()
        return {
            r["citekey"]: (r["external_citekey"] or r["citekey"])
            for r in rows
        }


def _safe_ext_ck(row: sqlite3.Row) -> Optional[str]:
    """Read external_citekey from a row, tolerating pre-v6 schemas in tests."""
    try:
        return row["external_citekey"]
    except (IndexError, KeyError):
        return None


def _safe_col(row: sqlite3.Row, col: str) -> Optional[str]:
    """Read a column from a sqlite3.Row, tolerating missing column in old schemas."""
    try:
        return row[col]
    except (IndexError, KeyError):
        return None
