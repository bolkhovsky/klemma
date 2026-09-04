"""LocalPaperStore — SQLite implementation of the PaperStore protocol (ADR-014).

Stores papers, fragments, and embeddings in ~/.klemma/library.db, shared across
all local projects. Enables deduplication: a paper processed once is available
to all projects without re-calling Claude or the embedding API.

Phase 1B: paper_id is a UUID assigned on first register_paper() call.
Phase 1C: LocalUserLibrary will map citekey → paper_id; for now callers that
need citekey-based lookup use find_paper(pdf_hash=...) or find_paper(doi=...).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import struct
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..models import FragmentRecord, PaperRecord

_VALID_CITATION_INTENTS = frozenset({
    "background", "method", "result_comparison", "extends", "contrasts", "uses_data"
})

# ------------------------------------------------------------------ #
# sqlite-vec optional extension                                        #
# ------------------------------------------------------------------ #

try:
    import sqlite_vec as _sqlite_vec  # type: ignore[import]
    _SQLITE_VEC_INSTALLED = True
except ImportError:
    _sqlite_vec = None  # type: ignore[assignment]
    _SQLITE_VEC_INSTALLED = False


def _check_vec_available() -> bool:
    """Return True if sqlite-vec loads successfully in a test in-memory DB."""
    if not _SQLITE_VEC_INSTALLED:
        return False
    try:
        test = sqlite3.connect(":memory:")
        test.enable_load_extension(True)
        _sqlite_vec.load(test)
        test.enable_load_extension(False)
        test.execute("SELECT vec_version()").fetchone()
        test.close()
        return True
    except Exception:
        return False


def _get_active_embedding_model() -> str:
    """Return active embedding model from env (set by SaaS worker), or empty string."""
    return os.environ.get("KLEMMA_EMBEDDINGS_MODEL", "")


# ------------------------------------------------------------------ #
# Schema                                                              #
# ------------------------------------------------------------------ #

# NOTE: library.db's user_version is co-owned with LocalUserLibrary (same file).
# LocalUserLibrary's migration chain gates `CREATE TABLE` on `version < 2` and
# subsequent ALTERs on `version < 3/4/5`, so THIS module must NOT bump
# user_version past 1 on a fresh DB — doing so would skip user_library's table
# creation and break its column migrations. All paper_store column additions
# are gated on idempotent `PRAGMA table_info()` prechecks instead. This DB
# chain is independent of the per-project state.py chain; they must not
# be merged.
_SCHEMA_VERSION = 1

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    paper_id  TEXT PRIMARY KEY,
    pdf_hash  TEXT UNIQUE,
    doi       TEXT,
    s2_paper_id TEXT,
    title     TEXT NOT NULL DEFAULT '',
    authors   TEXT,
    year      INTEGER,
    abstract  TEXT,
    raw_text  TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_papers_doi  ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_hash ON papers(pdf_hash);

CREATE TABLE IF NOT EXISTS extractions (
    extraction_id  TEXT PRIMARY KEY,
    paper_id       TEXT NOT NULL REFERENCES papers(paper_id),
    prompt_hash    TEXT NOT NULL,
    ai_model       TEXT NOT NULL,
    klemma_version TEXT,
    fragment_count INTEGER DEFAULT 0,
    extracted_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(paper_id, prompt_hash, ai_model)
);

CREATE TABLE IF NOT EXISTS fragments (
    fragment_id    TEXT PRIMARY KEY,
    paper_id       TEXT NOT NULL REFERENCES papers(paper_id),
    extraction_id  TEXT REFERENCES extractions(extraction_id),
    fragment_text  TEXT NOT NULL,
    fragment_type  TEXT,
    page_number    INTEGER,
    citation_intent TEXT,
    verbatim       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fragments_paper ON fragments(paper_id);

CREATE TABLE IF NOT EXISTS paper_embeddings (
    paper_id   TEXT NOT NULL REFERENCES papers(paper_id),
    model_name TEXT NOT NULL,
    vector     BLOB NOT NULL,
    dimensions INTEGER NOT NULL,
    computed_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (paper_id, model_name)
);

CREATE TABLE IF NOT EXISTS fragment_embeddings (
    fragment_id TEXT NOT NULL REFERENCES fragments(fragment_id),
    model_name  TEXT NOT NULL,
    vector      BLOB NOT NULL,
    dimensions  INTEGER NOT NULL,
    computed_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (fragment_id, model_name)
);

CREATE TABLE IF NOT EXISTS citation_graph (
    citing_paper_id TEXT NOT NULL REFERENCES papers(paper_id),
    cited_title_hash TEXT NOT NULL,
    cited_title  TEXT NOT NULL,
    cited_authors TEXT,
    cited_year    INTEGER,
    citation_intent TEXT,
    UNIQUE(citing_paper_id, cited_title_hash)
);
CREATE INDEX IF NOT EXISTS idx_citations_citing ON citation_graph(citing_paper_id);

CREATE TABLE IF NOT EXISTS recommendations_cache (
    user_id             TEXT NOT NULL,
    project_id          TEXT NOT NULL,
    library_state_hash  TEXT NOT NULL,
    outline_hash        TEXT NOT NULL,
    model               TEXT NOT NULL,
    json_result         TEXT NOT NULL,
    created_at          TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, project_id, library_state_hash, outline_hash, model)
);
CREATE INDEX IF NOT EXISTS idx_rec_cache_user_project
    ON recommendations_cache(user_id, project_id);
"""


_CREATE_ATTEMPTS = """
CREATE TABLE IF NOT EXISTS extraction_attempts (
    attempt_id          TEXT PRIMARY KEY,
    request_fingerprint TEXT,
    paper_id            TEXT NOT NULL REFERENCES papers(paper_id),
    prompt_name         TEXT,
    prompt_hash         TEXT,
    template_hash       TEXT,
    ai_model            TEXT,
    extractor_version   TEXT,
    klemma_version      TEXT,
    mode                TEXT NOT NULL DEFAULT 'standard',
    source_content_hash TEXT,
    chunk_size          INTEGER,
    chunk_overlap       INTEGER,
    min_chunk_chars     INTEGER,
    config_json         TEXT,
    coverage_json       TEXT,
    validation_incomplete INTEGER NOT NULL DEFAULT 0,
    started_at          TEXT DEFAULT (datetime('now')),
    finished_at         TEXT,
    status              TEXT NOT NULL DEFAULT 'running'
);
CREATE INDEX IF NOT EXISTS idx_attempts_paper ON extraction_attempts(paper_id);
CREATE INDEX IF NOT EXISTS idx_attempts_fingerprint ON extraction_attempts(request_fingerprint);

CREATE TABLE IF NOT EXISTS extraction_attempt_fragments (
    attempt_id      TEXT NOT NULL REFERENCES extraction_attempts(attempt_id),
    fragment_id     TEXT NOT NULL REFERENCES fragments(fragment_id),
    char_start      INTEGER,
    char_end        INTEGER,
    source_locator  TEXT,
    verbatim_status TEXT,
    PRIMARY KEY (attempt_id, fragment_id)
);
CREATE INDEX IF NOT EXISTS idx_attempt_frags_fragment ON extraction_attempt_fragments(fragment_id);
"""


class LocalPaperStore:
    """SQLite-backed PaperStore at ~/.klemma/library.db.

    Content-addressable: same PDF → same paper_id → same fragments (global dedup).
    Implements the PaperStore protocol from protocols.py.

    M1 addition: optional sqlite-vec KNN index (fragments_vec_user) for semantic
    fragment retrieval.  Falls back silently when the extension is unavailable.

    Usage::

        store = LocalPaperStore(Path.home() / ".klemma" / "library.db")
        paper_id = store.register_paper(title="...", pdf_hash="abc123", ...)
        frags = store.get_fragments(paper_id)
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._vec_enabled: bool = _check_vec_available()
        if _SQLITE_VEC_INSTALLED and not self._vec_enabled:
            logger.warning(
                "sqlite-vec installed but extension failed to load; semantic search disabled"
            )
        with self._conn() as conn:
            self._migrate_schema(conn)
        if self._vec_enabled:
            with self._conn() as conn:
                self._maybe_rebuild_vec_index(conn)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        if self._vec_enabled:
            try:
                conn.enable_load_extension(True)
                _sqlite_vec.load(conn)
                conn.enable_load_extension(False)
            except Exception as exc:
                logger.warning("sqlite-vec load failed on connection: %s; disabling", exc)
                self._vec_enabled = False
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
        if version < 1:
            conn.executescript(_CREATE_SCHEMA)
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

        # raw_text cache (papers) + verbatim flag (fragments). These are
        # gated on PRAGMA table_info — not user_version — because library.db's
        # version counter is co-owned with LocalUserLibrary (see note above).
        papers_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()
        }
        if papers_cols and "raw_text" not in papers_cols:
            conn.execute("ALTER TABLE papers ADD COLUMN raw_text TEXT")

        frag_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(fragments)").fetchall()
        }
        if frag_cols and "verbatim" not in frag_cols:
            conn.execute(
                "ALTER TABLE fragments ADD COLUMN verbatim INTEGER NOT NULL DEFAULT 0"
            )

        existing_tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "recommendations_cache" not in existing_tables:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS recommendations_cache (
                    user_id             TEXT NOT NULL,
                    project_id          TEXT NOT NULL,
                    library_state_hash  TEXT NOT NULL,
                    outline_hash        TEXT NOT NULL,
                    model               TEXT NOT NULL,
                    json_result         TEXT NOT NULL,
                    created_at          TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (user_id, project_id, library_state_hash, outline_hash, model)
                );
                CREATE INDEX IF NOT EXISTS idx_rec_cache_user_project
                    ON recommendations_cache(user_id, project_id);
            """)

        # ── extraction attempts (plan C2 / ADR-020) ───────────────────────
        # The legacy `extractions` table cannot represent repeated attempts
        # (UNIQUE(paper_id, prompt_hash, ai_model); one extraction_id per
        # fragment). Attempts + attempt↔fragment links live in their own
        # tables; created idempotently (no user_version bump — co-owned).
        if "extraction_attempts" not in existing_tables:
            conn.executescript(_CREATE_ATTEMPTS)
        attempt_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(extraction_attempts)").fetchall()
        }
        for col, typ in (
            ("request_fingerprint", "TEXT"),
            ("extractor_version", "TEXT"),
            ("validation_incomplete", "INTEGER NOT NULL DEFAULT 0"),
            ("coverage_json", "TEXT"),
        ):
            if attempt_cols and col not in attempt_cols:
                conn.execute(f"ALTER TABLE extraction_attempts ADD COLUMN {col} {typ}")

        # ── vec index tables (M1) ─────────────────────────────────────────
        # Created only when sqlite-vec is available. All three tables must be
        # present together; we check for fragments_vec_user_map (regular table)
        # as the canonical sentinel since virtual tables need the extension to
        # show up correctly.
        if self._vec_enabled and "fragments_vec_user_map" not in existing_tables:
            try:
                dim_row = conn.execute(
                    "SELECT dimensions FROM fragment_embeddings ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                dim = int(dim_row["dimensions"]) if dim_row else 1024
                conn.execute(f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS fragments_vec_user USING vec0(
                        user_id     TEXT partition key,
                        paper_id    TEXT,
                        fragment_id TEXT,
                        embedding   FLOAT[{dim}] distance_metric=cosine
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS fragments_vec_user_map (
                        user_id     TEXT NOT NULL,
                        fragment_id TEXT NOT NULL,
                        model_name  TEXT NOT NULL,
                        vec_rowid   INTEGER NOT NULL UNIQUE,
                        PRIMARY KEY (user_id, fragment_id, model_name)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS fragments_vec_state (
                        state_key   TEXT PRIMARY KEY,
                        state_value TEXT NOT NULL
                    )
                """)
                conn.execute(
                    "INSERT OR IGNORE INTO fragments_vec_state(state_key, state_value) VALUES (?, ?)",
                    ("dimensions", str(dim)),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO fragments_vec_state(state_key, state_value) VALUES (?, ?)",
                    ("active_model", ""),
                )
                logger.info("Vec index tables created (dim=%d)", dim)
            except Exception as exc:
                logger.warning("Failed to create vec index tables: %s; disabling vec", exc)
                self._vec_enabled = False

    # ------------------------------------------------------------------ #
    # Vec index — rebuild / backfill                                       #
    # ------------------------------------------------------------------ #

    def _maybe_rebuild_vec_index(self, conn: sqlite3.Connection) -> None:
        """Rebuild the KNN vec index if it's empty or the active model changed."""
        has_state = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fragments_vec_state'"
        ).fetchone()
        if not has_state:
            return

        state = {
            r["state_key"]: r["state_value"]
            for r in conn.execute("SELECT state_key, state_value FROM fragments_vec_state").fetchall()
        }
        stored_model = state.get("active_model", "")
        stored_dim = int(state.get("dimensions", "1024"))
        active_model = _get_active_embedding_model()

        vec_count = conn.execute("SELECT COUNT(*) FROM fragments_vec_user").fetchone()[0]

        # Determine the actual dimension for the new active model FIRST, so emb_count
        # uses the correct dimension filter. Computing emb_count with stored_dim would
        # return 0 when the new model has a different dimension, causing backfill to be
        # skipped even though embeddings exist.
        new_dim = stored_dim
        actual_dim_known = False
        if active_model:
            dim_row = conn.execute(
                "SELECT dimensions FROM fragment_embeddings WHERE model_name = ? LIMIT 1",
                (active_model,),
            ).fetchone()
            if dim_row:
                new_dim = int(dim_row["dimensions"])
                actual_dim_known = True

        emb_count = 0
        if active_model and actual_dim_known:
            emb_count = conn.execute(
                "SELECT COUNT(DISTINCT fe.fragment_id) FROM fragment_embeddings fe "
                "WHERE fe.model_name = ? AND fe.dimensions = ?",
                (active_model, new_dim),
            ).fetchone()[0]

        needs_rebuild = (
            (active_model and active_model != stored_model) or
            (vec_count == 0 and emb_count > 0)
        )
        if not needs_rebuild:
            return

        logger.info(
            "Vec index rebuild: model=%r (stored=%r), dim=%d (known=%s), vec_rows=%d, embs=%d",
            active_model, stored_model, new_dim, actual_dim_known, vec_count, emb_count,
        )

        # Drop + recreate virtual table (cleanest way to clear all vec rows).
        # If the true dim is not yet known (no embeddings for new model), use stored_dim
        # as placeholder. The first write via save_fragment_embedding() will detect the
        # mismatch and call _rebuild_vec_table_with_dim() to correct it.
        conn.executescript(f"""
            DROP TABLE IF EXISTS fragments_vec_user;
            DELETE FROM fragments_vec_user_map;
            CREATE VIRTUAL TABLE fragments_vec_user USING vec0(
                user_id     TEXT partition key,
                paper_id    TEXT,
                fragment_id TEXT,
                embedding   FLOAT[{new_dim}] distance_metric=cosine
            );
        """)

        inserted = 0
        if active_model and emb_count > 0:
            rows = conn.execute(
                """SELECT fe.fragment_id, fe.vector,
                          f.paper_id,
                          us.user_id
                   FROM fragment_embeddings fe
                   JOIN fragments f     ON f.fragment_id = fe.fragment_id
                   JOIN user_sources us ON us.paper_id = f.paper_id
                   WHERE fe.model_name = ? AND fe.dimensions = ?""",
                (active_model, new_dim),
            ).fetchall()
            for row in rows:
                try:
                    cur = conn.execute(
                        "INSERT INTO fragments_vec_user(user_id, paper_id, fragment_id, embedding) "
                        "VALUES (?, ?, ?, ?)",
                        (row["user_id"], row["paper_id"], row["fragment_id"], row["vector"]),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO fragments_vec_user_map"
                        "(user_id, fragment_id, model_name, vec_rowid) VALUES (?, ?, ?, ?)",
                        (row["user_id"], row["fragment_id"], active_model, cur.lastrowid),
                    )
                    inserted += 1
                except Exception as exc:
                    logger.warning("Vec backfill row failed: %s", exc)

        conn.execute(
            "INSERT OR REPLACE INTO fragments_vec_state(state_key, state_value) VALUES (?, ?)",
            ("active_model", active_model),
        )
        # Only commit dimensions to state when we have confirmed them from actual data.
        # When dim is unknown (no embeddings yet for new model), leave stored_dim as
        # placeholder so _rebuild_vec_table_with_dim() can correct it on first write.
        if actual_dim_known:
            conn.execute(
                "INSERT OR REPLACE INTO fragments_vec_state(state_key, state_value) VALUES (?, ?)",
                ("dimensions", str(new_dim)),
            )
        logger.info(
            "Vec index rebuilt: %d rows inserted (dim=%d, dim_confirmed=%s)",
            inserted, new_dim, actual_dim_known,
        )

    def _replace_vec_row_for_owner(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        paper_id: str,
        fragment_id: str,
        vector_blob: bytes,
        model_name: str,
    ) -> None:
        """Upsert one (user_id, fragment_id) pair in the vec index via companion map."""
        existing = conn.execute(
            "SELECT vec_rowid FROM fragments_vec_user_map "
            "WHERE user_id=? AND fragment_id=? AND model_name=?",
            (user_id, fragment_id, model_name),
        ).fetchone()
        if existing:
            conn.execute(
                "DELETE FROM fragments_vec_user WHERE rowid = ?",
                (existing["vec_rowid"],),
            )
        cur = conn.execute(
            "INSERT INTO fragments_vec_user(user_id, paper_id, fragment_id, embedding) "
            "VALUES (?, ?, ?, ?)",
            (user_id, paper_id, fragment_id, vector_blob),
        )
        conn.execute(
            "INSERT OR REPLACE INTO fragments_vec_user_map"
            "(user_id, fragment_id, model_name, vec_rowid) VALUES (?, ?, ?, ?)",
            (user_id, fragment_id, model_name, cur.lastrowid),
        )

    def _rebuild_vec_table_with_dim(
        self,
        conn: sqlite3.Connection,
        new_dim: int,
        model_name: str,
    ) -> None:
        """Drop and recreate fragments_vec_user with the correct dimension, then backfill.

        Called when a model switch happens before any embeddings for the new model existed
        at rebuild time, leaving the table with a placeholder dimension.  The first actual
        write (save_fragment_embedding) detects the mismatch and calls this method.
        executescript() issues an implicit COMMIT first, so the caller's pending writes
        are committed before the table is recreated — this is intentional.
        """
        conn.executescript(f"""
            DROP TABLE IF EXISTS fragments_vec_user;
            DELETE FROM fragments_vec_user_map;
            CREATE VIRTUAL TABLE fragments_vec_user USING vec0(
                user_id     TEXT partition key,
                paper_id    TEXT,
                fragment_id TEXT,
                embedding   FLOAT[{new_dim}] distance_metric=cosine
            );
        """)
        conn.execute(
            "INSERT OR REPLACE INTO fragments_vec_state(state_key, state_value) VALUES (?, ?)",
            ("dimensions", str(new_dim)),
        )
        rows = conn.execute(
            """SELECT fe.fragment_id, fe.vector, f.paper_id, us.user_id
               FROM fragment_embeddings fe
               JOIN fragments f     ON f.fragment_id = fe.fragment_id
               JOIN user_sources us ON us.paper_id = f.paper_id
               WHERE fe.model_name = ? AND fe.dimensions = ?""",
            (model_name, new_dim),
        ).fetchall()
        inserted = 0
        for row in rows:
            try:
                cur = conn.execute(
                    "INSERT INTO fragments_vec_user(user_id, paper_id, fragment_id, embedding) "
                    "VALUES (?, ?, ?, ?)",
                    (row["user_id"], row["paper_id"], row["fragment_id"], row["vector"]),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO fragments_vec_user_map"
                    "(user_id, fragment_id, model_name, vec_rowid) VALUES (?, ?, ?, ?)",
                    (row["user_id"], row["fragment_id"], model_name, cur.lastrowid),
                )
                inserted += 1
            except Exception as exc:
                logger.warning("Vec backfill row failed during dim-fix rebuild: %s", exc)
        logger.info(
            "Vec table rebuilt with correct dim=%d, model=%r, %d rows",
            new_dim, model_name, inserted,
        )

    # ------------------------------------------------------------------ #
    # Paper registry                                                       #
    # ------------------------------------------------------------------ #

    def find_paper(
        self,
        *,
        pdf_hash: Optional[str] = None,
        doi: Optional[str] = None,
    ) -> Optional["PaperRecord"]:
        """Look up a paper by pdf_hash or doi. Returns None if not found."""
        from ..models import PaperRecord

        with self._conn() as conn:
            if pdf_hash:
                row = conn.execute(
                    "SELECT paper_id, pdf_hash, doi, title, authors, year, abstract FROM papers WHERE pdf_hash = ?", (pdf_hash,)
                ).fetchone()
                if row:
                    return _row_to_paper(row, PaperRecord)
            if doi:
                row = conn.execute(
                    "SELECT paper_id, pdf_hash, doi, title, authors, year, abstract FROM papers WHERE doi = ?", (doi,)
                ).fetchone()
                if row:
                    return _row_to_paper(row, PaperRecord)
        return None

    def get_paper_by_id(self, paper_id: str) -> Optional["PaperRecord"]:
        """Look up a paper by its paper_id. Returns None if not found."""
        from ..models import PaperRecord

        with self._conn() as conn:
            row = conn.execute(
                "SELECT paper_id, pdf_hash, doi, title, authors, year, abstract FROM papers WHERE paper_id = ?", (paper_id,)
            ).fetchone()
        if not row:
            return None
        return _row_to_paper(row, PaperRecord)

    def register_paper(
        self,
        *,
        title: str,
        authors: str = "",
        year: Optional[int] = None,
        doi: Optional[str] = None,
        abstract: str = "",
        pdf_hash: str,
    ) -> str:
        """Register a paper; return paper_id. Idempotent: same pdf_hash → same id."""
        existing = self.find_paper(pdf_hash=pdf_hash)
        if existing:
            return existing.paper_id
        if doi:
            existing = self.find_paper(doi=doi)
            if existing:
                # Update pdf_hash on existing DOI match if missing
                with self._conn() as conn:
                    conn.execute(
                        "UPDATE papers SET pdf_hash = ? WHERE paper_id = ? AND pdf_hash IS NULL",
                        (pdf_hash, existing.paper_id),
                    )
                return existing.paper_id

        paper_id = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO papers
                   (paper_id, pdf_hash, doi, title, authors, year, abstract)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (paper_id, pdf_hash or None, doi or None, title, authors, year, abstract),
            )
        return paper_id

    def update_paper_raw_text(self, paper_id: str, raw_text: str) -> bool:
        """Persist the PDF's extracted text for the verbatim validator + future
        raw-text search. Idempotent: overwrites on repeated calls.
        """
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE papers SET raw_text = ? WHERE paper_id = ?",
                (raw_text, paper_id),
            )
        return cursor.rowcount > 0

    def get_raw_text(self, paper_id: str) -> Optional[str]:
        """Return the cached raw PDF text, or None if not yet populated."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT raw_text FROM papers WHERE paper_id = ?", (paper_id,)
            ).fetchone()
        if not row or row["raw_text"] is None:
            return None
        return row["raw_text"]

    def update_paper_metadata(
        self,
        paper_id: str,
        *,
        title: str = "",
        authors: str = "",
        year: Optional[int] = None,
        doi: Optional[str] = None,
        abstract: str = "",
    ) -> bool:
        """Update metadata for an existing paper. Only non-empty fields are updated."""
        updates: list[str] = []
        params: list[object] = []
        if title:
            updates.append("title = ?")
            params.append(title)
        if authors:
            updates.append("authors = ?")
            params.append(authors)
        if year is not None:
            updates.append("year = ?")
            params.append(year)
        if doi:
            updates.append("doi = ?")
            params.append(doi)
        if abstract:
            updates.append("abstract = ?")
            params.append(abstract)
        if not updates:
            return False
        params.append(paper_id)
        with self._conn() as conn:
            cursor = conn.execute(
                f"UPDATE papers SET {', '.join(updates)} WHERE paper_id = ?",
                tuple(params),
            )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------ #
    # Fragments                                                            #
    # ------------------------------------------------------------------ #

    def get_paper_ids_with_raw_text(self) -> list[str]:
        """Return paper_ids whose raw_text cache is populated.

        Used by the ``backfill-verbatim`` subcommand to find papers whose
        fragments can be revalidated without re-extracting the PDF.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT paper_id FROM papers WHERE raw_text IS NOT NULL AND raw_text != ''",
            ).fetchall()
        return [row["paper_id"] for row in rows]

    def update_fragment_verbatim(self, fragment_id: str, verbatim: bool) -> bool:
        """Flip a fragment's verbatim flag; returns True when a row was updated."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE fragments SET verbatim = ? WHERE fragment_id = ?",
                (1 if verbatim else 0, fragment_id),
            )
            return cur.rowcount > 0

    def delete_fragments(self, paper_id: str) -> int:
        """Delete all fragments and extractions for paper_id. Returns count deleted.

        Deletion order: vec index → vec map → fragment_embeddings → fragments → extractions.
        This satisfies FK constraints (fragment_embeddings references fragments).
        """
        with self._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM fragments WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
            frag_ids = [
                r[0] for r in conn.execute(
                    "SELECT fragment_id FROM fragments WHERE paper_id = ?", (paper_id,)
                ).fetchall()
            ]
            if frag_ids:
                placeholders = ",".join("?" * len(frag_ids))
                # Clean vec index entries (vec_rowids → virtual table rows → map rows)
                if self._vec_enabled:
                    try:
                        vec_rows = conn.execute(
                            f"SELECT vec_rowid FROM fragments_vec_user_map "
                            f"WHERE fragment_id IN ({placeholders})",
                            frag_ids,
                        ).fetchall()
                        if vec_rows:
                            conn.executemany(
                                "DELETE FROM fragments_vec_user WHERE rowid = ?",
                                [(r["vec_rowid"],) for r in vec_rows],
                            )
                        conn.execute(
                            f"DELETE FROM fragments_vec_user_map "
                            f"WHERE fragment_id IN ({placeholders})",
                            frag_ids,
                        )
                    except Exception as exc:
                        logger.warning("Vec cleanup in delete_fragments failed: %s", exc)
                # FK order: fragment_embeddings before fragments
                conn.execute(
                    f"DELETE FROM fragment_embeddings WHERE fragment_id IN ({placeholders})",
                    frag_ids,
                )
            conn.execute("DELETE FROM fragments WHERE paper_id = ?", (paper_id,))
            conn.execute("DELETE FROM extractions WHERE paper_id = ?", (paper_id,))
        return count

    def get_fragments(self, paper_id: str) -> list["FragmentRecord"]:
        """Return all fragments stored for paper_id."""
        from ..models import FragmentRecord

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM fragments WHERE paper_id = ? ORDER BY rowid",
                (paper_id,),
            ).fetchall()
        return [
            FragmentRecord(
                fragment_id=row["fragment_id"],
                paper_id=row["paper_id"],
                fragment_text=row["fragment_text"],
                fragment_type=row["fragment_type"] or "key_idea",
                page_number=row["page_number"],
                citation_intent=row["citation_intent"],
                verbatim=bool(row["verbatim"]) if "verbatim" in row.keys() else False,
                content_hash=row["fragment_id"],
            )
            for row in rows
        ]

    def save_fragments(
        self,
        paper_id: str,
        fragments: list["FragmentRecord"],
        prompt_hash: str,
        ai_model: str,
    ) -> int:
        """Save fragments for paper_id; return count inserted (skips duplicates)."""
        try:
            from .. import __version__ as _kv
        except Exception:
            _kv = "unknown"

        extraction_id = str(uuid.uuid4())
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO extractions
                   (extraction_id, paper_id, prompt_hash, ai_model, klemma_version, fragment_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (extraction_id, paper_id, prompt_hash, ai_model, _kv, len(fragments)),
            )
            if cur.rowcount == 0:
                # Row already exists (UNIQUE conflict on paper_id+prompt_hash+ai_model)
                # Use the existing extraction_id so fragment FK constraint is satisfied
                row = conn.execute(
                    "SELECT extraction_id FROM extractions WHERE paper_id=? AND prompt_hash=? AND ai_model=?",
                    (paper_id, prompt_hash, ai_model),
                ).fetchone()
                if row:
                    extraction_id = row["extraction_id"]
            inserted = 0
            for f in fragments:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO fragments
                       (fragment_id, paper_id, extraction_id,
                        fragment_text, fragment_type, page_number,
                        citation_intent, verbatim)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f.fragment_id,
                        paper_id,
                        extraction_id,
                        f.fragment_text,
                        f.fragment_type,
                        f.page_number,
                        f.citation_intent,
                        1 if f.verbatim else 0,
                    ),
                )
                inserted += cur.rowcount
        return inserted

    # ------------------------------------------------------------------ #
    # Citation graph                                                       #
    # ------------------------------------------------------------------ #


    # ------------------------------------------------------------------ #
    # Extraction attempts (plan C2 / ADR-020)                              #
    # ------------------------------------------------------------------ #

    def start_attempt(
        self,
        attempt_id: str,
        paper_id: str,
        *,
        request_fingerprint: str = "",
        prompt_name: str = "",
        prompt_hash: str = "",
        template_hash: str = "",
        ai_model: str = "",
        extractor_version: str = "",
        klemma_version: str = "",
        mode: str = "standard",
        source_content_hash: str = "",
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        min_chunk_chars: Optional[int] = None,
        config_json: str = "",
    ) -> None:
        """Record an attempt as ``running`` before the first AI call (idempotent)."""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO extraction_attempts
                   (attempt_id, request_fingerprint, paper_id, prompt_name, prompt_hash,
                    template_hash, ai_model, extractor_version, klemma_version, mode,
                    source_content_hash, chunk_size, chunk_overlap, min_chunk_chars,
                    config_json, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')""",
                (
                    attempt_id, request_fingerprint, paper_id, prompt_name, prompt_hash,
                    template_hash, ai_model, extractor_version, klemma_version, mode,
                    source_content_hash, chunk_size, chunk_overlap, min_chunk_chars,
                    config_json,
                ),
            )

    def finish_attempt(
        self,
        attempt_id: str,
        *,
        status: str,
        coverage_json: str = "",
        validation_incomplete: bool = False,
    ) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE extraction_attempts
                   SET status=?, coverage_json=?, validation_incomplete=?,
                       finished_at=datetime('now')
                   WHERE attempt_id=?""",
                (status, coverage_json, 1 if validation_incomplete else 0, attempt_id),
            )
            return cur.rowcount > 0

    def save_attempt_fragments(
        self,
        attempt_id: str,
        paper_id: str,
        fragments: list["FragmentRecord"],
        links: list[dict],
    ) -> int:
        """Idempotently store canonical fragments and link them to the attempt.

        ``links`` are parallel to ``fragments``: dicts with ``char_start``,
        ``char_end``, ``source_locator``, ``verbatim_status``. Canonical rows
        are never deleted; a re-extracted text re-links to the same
        content-hash row. Returns the number of link rows written/updated.
        """
        written = 0
        with self._conn() as conn:
            for f, link in zip(fragments, links):
                conn.execute(
                    """INSERT OR IGNORE INTO fragments
                       (fragment_id, paper_id, extraction_id, fragment_text, fragment_type,
                        page_number, citation_intent, verbatim)
                       VALUES (?, ?, NULL, ?, ?, ?, ?, ?)""",
                    (
                        f.fragment_id, paper_id, f.fragment_text, f.fragment_type,
                        f.page_number, f.citation_intent, 1 if f.verbatim else 0,
                    ),
                )
                # Canonical verbatim flag follows the latest attempt for this text.
                conn.execute(
                    "UPDATE fragments SET verbatim=? WHERE fragment_id=?",
                    (1 if f.verbatim else 0, f.fragment_id),
                )
                cur = conn.execute(
                    """INSERT INTO extraction_attempt_fragments
                       (attempt_id, fragment_id, char_start, char_end, source_locator,
                        verbatim_status)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(attempt_id, fragment_id) DO UPDATE SET
                         char_start=excluded.char_start, char_end=excluded.char_end,
                         source_locator=excluded.source_locator,
                         verbatim_status=excluded.verbatim_status""",
                    (
                        attempt_id, f.fragment_id, link.get("char_start"),
                        link.get("char_end"), link.get("source_locator"),
                        link.get("verbatim_status"),
                    ),
                )
                written += cur.rowcount
        return written

    def get_attempt(self, attempt_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM extraction_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_attempts(self, paper_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM extraction_attempts WHERE paper_id=? ORDER BY started_at, rowid",
                (paper_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_attempt_fragments(self, attempt_id: str) -> list[dict]:
        """Fragments linked to an attempt with their span/locator snapshot."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT f.fragment_id, f.paper_id, f.fragment_text, f.fragment_type,
                          f.page_number, f.citation_intent, f.verbatim,
                          l.char_start, l.char_end, l.source_locator, l.verbatim_status
                   FROM extraction_attempt_fragments l
                   JOIN fragments f ON f.fragment_id = l.fragment_id
                   WHERE l.attempt_id=? ORDER BY l.rowid""",
                (attempt_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_attempt_fragment_provenance(
        self,
        attempt_id: str,
        fragment_id: str,
        *,
        char_start: Optional[int],
        char_end: Optional[int],
        source_locator: Optional[str],
        verbatim_status: str,
    ) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE extraction_attempt_fragments
                   SET char_start=?, char_end=?, source_locator=?, verbatim_status=?
                   WHERE attempt_id=? AND fragment_id=?""",
                (char_start, char_end, source_locator, verbatim_status, attempt_id, fragment_id),
            )
            return cur.rowcount > 0

    def find_orphan_attempts(self, referenced_attempt_ids: set[str]) -> list[dict]:
        """Attempts not referenced by any project run — safe leftovers of a
        failure between the library write and the project publish."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM extraction_attempts").fetchall()
        return [dict(r) for r in rows if r["attempt_id"] not in referenced_attempt_ids]

    def save_citation_links(self, paper_id: str, references: list[dict]) -> int:
        """Save citation links from a paper's bibliography to citation_graph.

        Each reference: {"title": str, "authors": str, "year": int|None}.
        Returns count of links saved.
        """
        import hashlib

        saved = 0
        with self._conn() as conn:
            for ref in references:
                title = (ref.get("title") or "").strip()
                if not title:
                    continue
                title_hash = hashlib.md5(title.lower().encode()).hexdigest()
                _raw_intent = ref.get("citation_intent")
                _intent = _raw_intent if _raw_intent in _VALID_CITATION_INTENTS else None
                conn.execute(
                    """INSERT OR IGNORE INTO citation_graph
                       (citing_paper_id, cited_title_hash, cited_title,
                        cited_authors, cited_year, citation_intent)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        paper_id,
                        title_hash,
                        title,
                        ref.get("authors", ""),
                        ref.get("year"),
                        _intent,
                    ),
                )
                saved += 1
        return saved

    # ------------------------------------------------------------------ #
    # Citation graph — reference gaps                                      #
    # ------------------------------------------------------------------ #

    def get_reference_gaps(
        self,
        *,
        paper_ids: list[str],
        user_id: str,
        limit: int = 200,
    ) -> tuple[list[dict], dict[str, list[str]]]:
        """Return (gaps, citing_paper_ids_by_gap_hash).

        Two-step query to avoid GROUP_CONCAT truncation for paper_ids.
        gaps: list of dicts with keys: cited_title_hash, title, authors, year,
              count, intents, avg_quality
        citing_paper_ids_by_gap_hash: {cited_title_hash: [paper_id, ...]}
        """
        if not paper_ids:
            return [], {}

        placeholders = ",".join("?" for _ in paper_ids)

        with self._conn() as conn:
            # Step 1 — aggregate: count citations and collect intents per gap
            rows = conn.execute(
                f"""SELECT
                     cg.cited_title_hash,
                     cg.cited_title as title,
                     cg.cited_authors as authors,
                     cg.cited_year as year,
                     COUNT(DISTINCT cg.citing_paper_id) as count,
                     GROUP_CONCAT(cg.citation_intent) as intents,
                     AVG(COALESCE(us.quality_score, 3)) as avg_quality
                   FROM citation_graph cg
                   LEFT JOIN user_sources us
                     ON cg.citing_paper_id = us.paper_id AND us.user_id = ?
                   WHERE cg.citing_paper_id IN ({placeholders})
                     AND NOT EXISTS (
                       SELECT 1 FROM papers p
                       INNER JOIN user_sources us_own
                         ON us_own.paper_id = p.paper_id AND us_own.user_id = ?
                       WHERE LOWER(TRIM(p.title)) = LOWER(TRIM(cg.cited_title))
                     )
                   GROUP BY cg.cited_title_hash
                   ORDER BY count DESC
                   LIMIT ?""",
                (user_id, *paper_ids, user_id, limit),
            ).fetchall()

            gaps = [dict(r) for r in rows]

            if not gaps:
                return [], {}

            gap_hashes = [g["cited_title_hash"] for g in gaps]
            hash_placeholders = ",".join("?" for _ in gap_hashes)

            # Step 2 — fetch citing paper IDs for each gap hash
            citing_rows = conn.execute(
                f"""SELECT cited_title_hash, citing_paper_id
                    FROM citation_graph
                    WHERE cited_title_hash IN ({hash_placeholders})
                      AND citing_paper_id IN ({placeholders})""",
                (*gap_hashes, *paper_ids),
            ).fetchall()

        citing_ids_by_hash: dict[str, list[str]] = {}
        for row in citing_rows:
            h = row["cited_title_hash"]
            pid = row["citing_paper_id"]
            citing_ids_by_hash.setdefault(h, []).append(pid)

        return gaps, citing_ids_by_hash

    def count_citation_gaps(self, paper_ids: list[str] | None = None) -> int:
        """Count unique cited papers not in the library.

        When paper_ids is provided, only count gaps from those papers (user-scoped).
        """
        with self._conn() as conn:
            if paper_ids:
                placeholders = ",".join("?" for _ in paper_ids)
                row = conn.execute(
                    f"""SELECT COUNT(DISTINCT cg.cited_title_hash)
                       FROM citation_graph cg
                       WHERE cg.citing_paper_id IN ({placeholders})
                         AND NOT EXISTS (
                           SELECT 1 FROM papers p
                           WHERE LOWER(TRIM(p.title)) = LOWER(TRIM(cg.cited_title))
                         )""",
                    paper_ids,
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT COUNT(DISTINCT cg.cited_title_hash)
                       FROM citation_graph cg
                       WHERE NOT EXISTS (
                         SELECT 1 FROM papers p
                         WHERE LOWER(TRIM(p.title)) = LOWER(TRIM(cg.cited_title))
                       )"""
                ).fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------ #
    # Paper-level embeddings                                               #
    # ------------------------------------------------------------------ #

    def get_paper_embedding(
        self, paper_id: str, model: str
    ) -> Optional[list[float]]:
        """Return stored embedding vector or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT vector, dimensions FROM paper_embeddings WHERE paper_id=? AND model_name=?",
                (paper_id, model),
            ).fetchone()
        if row:
            return list(struct.unpack(f"{row['dimensions']}f", row["vector"]))
        return None

    def save_paper_embedding(
        self, paper_id: str, vector: list[float], model: str
    ) -> None:
        """Upsert a paper-level embedding."""
        blob = struct.pack(f"{len(vector)}f", *vector)
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO paper_embeddings
                   (paper_id, model_name, vector, dimensions)
                   VALUES (?, ?, ?, ?)""",
                (paper_id, model, blob, len(vector)),
            )

    # ------------------------------------------------------------------ #
    # Fragment-level embeddings                                            #
    # ------------------------------------------------------------------ #

    def get_fragment_embeddings(
        self, paper_id: str, model: str
    ) -> dict[str, list[float]]:
        """Return {fragment_id: vector} for all fragments of paper_id."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT fe.fragment_id, fe.vector, fe.dimensions
                   FROM fragment_embeddings fe
                   JOIN fragments f ON fe.fragment_id = f.fragment_id
                   WHERE f.paper_id = ? AND fe.model_name = ?""",
                (paper_id, model),
            ).fetchall()
        return {
            row["fragment_id"]: list(
                struct.unpack(f"{row['dimensions']}f", row["vector"])
            )
            for row in rows
        }

    def save_fragment_embedding(
        self, fragment_id: str, vector: list[float], model: str
    ) -> None:
        """Upsert a fragment-level embedding, with dual-write to vec index."""
        blob = struct.pack(f"{len(vector)}f", *vector)
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO fragment_embeddings
                   (fragment_id, model_name, vector, dimensions)
                   VALUES (?, ?, ?, ?)""",
                (fragment_id, model, blob, len(vector)),
            )
            # Dual-write to vec index when this is the active embedding model
            if self._vec_enabled and model == _get_active_embedding_model():
                owners = conn.execute(
                    """SELECT DISTINCT us.user_id, f.paper_id
                       FROM fragments f
                       JOIN user_sources us ON us.paper_id = f.paper_id
                       WHERE f.fragment_id = ?""",
                    (fragment_id,),
                ).fetchall()
                _dim_rebuilt = False
                for owner in owners:
                    try:
                        self._replace_vec_row_for_owner(
                            conn,
                            user_id=owner["user_id"],
                            paper_id=owner["paper_id"],
                            fragment_id=fragment_id,
                            vector_blob=blob,
                            model_name=model,
                        )
                    except Exception as exc:
                        exc_msg = str(exc).lower()
                        if not _dim_rebuilt and ("dimension" in exc_msg or "mismatch" in exc_msg):
                            # Vec table was created with a placeholder dimension (model
                            # switch before any embeddings for the new model existed).
                            # Rebuild once with the actual dimension and retry all owners.
                            actual_dim = len(vector)
                            logger.info(
                                "Vec dim mismatch on first write (dim=%d, model=%r); rebuilding",
                                actual_dim, model,
                            )
                            try:
                                self._rebuild_vec_table_with_dim(conn, actual_dim, model)
                                _dim_rebuilt = True
                                self._replace_vec_row_for_owner(
                                    conn,
                                    user_id=owner["user_id"],
                                    paper_id=owner["paper_id"],
                                    fragment_id=fragment_id,
                                    vector_blob=blob,
                                    model_name=model,
                                )
                            except Exception as rebuild_exc:
                                logger.warning(
                                    "Vec rebuild-and-retry failed for %s: %s",
                                    fragment_id, rebuild_exc,
                                )
                        else:
                            logger.warning("Vec dual-write failed for %s: %s", fragment_id, exc)

    def get_paper_embeddings_batch(
        self, paper_ids: list[str], model: Optional[str] = None
    ) -> dict[str, list[float]]:
        """Return {paper_id: embedding_vector} for paper_ids that have embeddings."""
        if not paper_ids:
            return {}
        with self._conn() as conn:
            if model:
                placeholders = ",".join("?" for _ in paper_ids)
                rows = conn.execute(
                    f"SELECT paper_id, vector FROM paper_embeddings WHERE paper_id IN ({placeholders}) AND model_name = ?",
                    (*paper_ids, model),
                ).fetchall()
            else:
                # Get most recently inserted embedding per paper (any model).
                # MAX(rowid) is deterministic — avoids returning mixed-model vectors
                # from arbitrary GROUP BY which can produce different dimensions.
                placeholders = ",".join("?" for _ in paper_ids)
                rows = conn.execute(
                    f"""SELECT paper_id, vector FROM paper_embeddings
                        WHERE rowid IN (
                            SELECT MAX(rowid) FROM paper_embeddings
                            WHERE paper_id IN ({placeholders})
                            GROUP BY paper_id
                        )""",
                    paper_ids,
                ).fetchall()
        result = {}
        for row in rows:
            try:
                vec = list(struct.unpack(f"{len(row['vector']) // 4}f", row["vector"]))
                result[row["paper_id"]] = vec
            except Exception:
                pass
        return result

    def get_latest_embedding_dim(self, paper_ids: list[str]) -> Optional[int]:
        """Return the vector dimension of the most recently inserted embedding
        for any of the given papers.

        Used by the scoring pipeline to pin all vectors to the current active
        model's dimension after a migration — deterministic regardless of how
        many papers still carry stale embeddings of a different size.
        """
        if not paper_ids:
            return None
        placeholders = ",".join("?" for _ in paper_ids)
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT dimensions FROM paper_embeddings"
                f" WHERE paper_id IN ({placeholders})"
                f" ORDER BY rowid DESC LIMIT 1",
                paper_ids,
            ).fetchone()
        return row["dimensions"] if row else None

    # ------------------------------------------------------------------ #
    # Semantic fragment search (M1)                                        #
    # ------------------------------------------------------------------ #

    def find_similar_fragments(
        self,
        query_vector: list[float],
        user_id: str,
        limit: int = 20,
        citekey_filter: Optional[str] = None,
    ) -> list[dict]:
        """KNN search for fragments semantically closest to query_vector.

        Searches only within the given user's library using the per-user vec index.
        Returns list of dicts: {fragment_id, fragment_text, paper_id, citekey, similarity}.
        Returns [] when vec index is unavailable — callers must handle gracefully.
        """
        if not self._vec_enabled:
            return []

        query_blob = struct.pack(f"{len(query_vector)}f", *query_vector)
        try:
            with self._conn() as conn:
                if citekey_filter:
                    paper_row = conn.execute(
                        "SELECT paper_id FROM user_sources WHERE citekey = ? AND user_id = ?",
                        (citekey_filter, user_id),
                    ).fetchone()
                    if not paper_row:
                        return []
                    paper_id_filter = paper_row["paper_id"]
                    rows = conn.execute(
                        """SELECT fv.fragment_id,
                                  fv.distance,
                                  f.fragment_text,
                                  f.paper_id,
                                  f.page_number,
                                  us.citekey
                           FROM fragments_vec_user fv
                           JOIN fragments f     ON fv.fragment_id = f.fragment_id
                           JOIN user_sources us ON f.paper_id = us.paper_id AND us.user_id = ?
                           WHERE fv.embedding MATCH ?
                             AND k = ?
                             AND fv.user_id = ?
                             AND fv.paper_id = ?""",
                        (user_id, query_blob, limit, user_id, paper_id_filter),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT fv.fragment_id,
                                  fv.distance,
                                  f.fragment_text,
                                  f.paper_id,
                                  f.page_number,
                                  us.citekey
                           FROM fragments_vec_user fv
                           JOIN fragments f     ON fv.fragment_id = f.fragment_id
                           JOIN user_sources us ON f.paper_id = us.paper_id AND us.user_id = ?
                           WHERE fv.embedding MATCH ?
                             AND k = ?
                             AND fv.user_id = ?""",
                        (user_id, query_blob, limit, user_id),
                    ).fetchall()
        except Exception as exc:
            logger.warning("Semantic fragment search failed: %s", exc)
            return []

        return [
            {
                "fragment_id": row["fragment_id"],
                "fragment_text": row["fragment_text"],
                "paper_id": row["paper_id"],
                "citekey": row["citekey"],
                "page_number": row["page_number"],
                "similarity": max(0.0, 1.0 - row["distance"]),
            }
            for row in rows
        ]

    def ensure_vec_entries_for_user_paper(self, user_id: str, paper_id: str) -> int:
        """Populate vec index for a user–paper pair that already has embeddings.

        Called when a user adds an already-processed paper (dedup / attach path)
        so semantic search works immediately without waiting for a global rebuild.
        Returns number of vec rows created.
        """
        if not self._vec_enabled:
            return 0
        active_model = _get_active_embedding_model()
        if not active_model:
            return 0

        with self._conn() as conn:
            dim_row = conn.execute(
                "SELECT state_value FROM fragments_vec_state WHERE state_key = 'dimensions'"
            ).fetchone()
            stored_dim = int(dim_row["state_value"]) if dim_row else 1024

            rows = conn.execute(
                """SELECT fe.fragment_id, fe.vector
                   FROM fragment_embeddings fe
                   JOIN fragments f ON f.fragment_id = fe.fragment_id
                   WHERE f.paper_id = ? AND fe.model_name = ? AND fe.dimensions = ?""",
                (paper_id, active_model, stored_dim),
            ).fetchall()

            created = 0
            for row in rows:
                try:
                    self._replace_vec_row_for_owner(
                        conn,
                        user_id=user_id,
                        paper_id=paper_id,
                        fragment_id=row["fragment_id"],
                        vector_blob=row["vector"],
                        model_name=active_model,
                    )
                    created += 1
                except Exception as exc:
                    logger.warning(
                        "ensure_vec_entries: failed for %s/%s: %s",
                        paper_id, row["fragment_id"], exc,
                    )

        if created:
            logger.info(
                "ensure_vec_entries: %d rows added for paper %s user %s",
                created, paper_id, user_id,
            )
        return created

    # ------------------------------------------------------------------ #
    # Citation graph — backfill                                            #
    # ------------------------------------------------------------------ #

    def update_citation_intents(self, paper_id: str, refs: list[dict]) -> int:
        """Update citation_intent for existing citation_graph entries (backfill).

        Matches by (citing_paper_id, cited_title_hash).
        Only updates entries where current intent IS NULL — 'background' is now a
        valid intent (Teufel 2006 taxonomy) and must not be overwritten by re-runs.
        Returns count of updated rows.
        """
        import hashlib as _hashlib

        updated = 0
        with self._conn() as conn:
            for ref in refs:
                title = (ref.get("title") or "").strip()
                if not title:
                    continue
                raw_intent = ref.get("citation_intent")
                if raw_intent not in _VALID_CITATION_INTENTS:
                    if raw_intent is not None:
                        logger.warning(
                            "update_citation_intents: invalid intent %r for %s, skipping",
                            raw_intent, title,
                        )
                    continue  # only update if we have a valid non-null intent
                title_hash = _hashlib.md5(title.lower().encode()).hexdigest()
                cur = conn.execute(
                    """UPDATE citation_graph
                       SET citation_intent = ?
                       WHERE citing_paper_id = ? AND cited_title_hash = ?
                         AND citation_intent IS NULL""",
                    (raw_intent, paper_id, title_hash),
                )
                updated += cur.rowcount
        return updated

    def get_papers_for_user_backfill(
        self,
        user_id: str,
        batch_size: int = 20,
        cursor: Optional[str] = None,
    ) -> tuple[list[dict], int]:
        """Return papers needing citation intent backfill, with cursor-based pagination.

        Returns (papers_batch, remaining_count).
        Each paper dict has: paper_id, title.

        Only returns papers where:
        - At least one citation_graph entry has intent IS NULL (not 'background' —
          that is now a valid intent that must not be overwritten by re-runs)
        - raw_text IS NOT NULL (paper has processable text; otherwise no AI call possible)

        remaining_count is the total across ALL cursor positions — not filtered by
        cursor — so failed papers that are behind the cursor still appear in the count
        and the caller can detect that the loop finished with unresolved work.
        """
        with self._conn() as conn:
            # Remaining = total processable papers still with NULL intents (no cursor filter).
            # Cursor-independence is intentional: failed papers behind the cursor still count.
            remaining = conn.execute(
                """SELECT COUNT(DISTINCT p.paper_id) FROM papers p
                   JOIN user_sources us ON p.paper_id = us.paper_id AND us.user_id = ?
                   JOIN citation_graph cg ON cg.citing_paper_id = p.paper_id
                   WHERE cg.citation_intent IS NULL
                     AND p.raw_text IS NOT NULL""",
                (user_id,),
            ).fetchone()[0]

            # Fetch batch (cursor-based pagination for the loop)
            if cursor:
                rows = conn.execute(
                    """SELECT DISTINCT p.paper_id, p.title FROM papers p
                       JOIN user_sources us ON p.paper_id = us.paper_id AND us.user_id = ?
                       JOIN citation_graph cg ON cg.citing_paper_id = p.paper_id
                       WHERE cg.citation_intent IS NULL
                         AND p.raw_text IS NOT NULL
                         AND p.paper_id > ?
                       ORDER BY p.paper_id ASC
                       LIMIT ?""",
                    (user_id, cursor, batch_size),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT DISTINCT p.paper_id, p.title FROM papers p
                       JOIN user_sources us ON p.paper_id = us.paper_id AND us.user_id = ?
                       JOIN citation_graph cg ON cg.citing_paper_id = p.paper_id
                       WHERE cg.citation_intent IS NULL
                         AND p.raw_text IS NOT NULL
                       ORDER BY p.paper_id ASC
                       LIMIT ?""",
                    (user_id, batch_size),
                ).fetchall()
        papers = [{"paper_id": r["paper_id"], "title": r["title"]} for r in rows]
        return papers, remaining

    # ---------------------------------------------------------------- #
    # Multi-user safety                                                 #
    # ---------------------------------------------------------------- #

    def count_paper_owners(self, paper_id: str) -> int:
        """Count distinct users referencing this paper_id in user_sources.

        Used to guard force-reprocess: if >1 user owns the paper, deleting
        its fragments would affect all of them.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM user_sources WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()
        return row[0] if row else 0

    # ---------------------------------------------------------------- #
    # Fragment search (keyword — SaaS)                                  #
    # ---------------------------------------------------------------- #

    def search_fragments_for_user(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
    ) -> list[dict]:
        """Full-text keyword search over fragments belonging to a user's library.

        Joins ``fragments`` → ``papers`` → ``user_sources`` (all in the same
        library.db).  Filters by ``user_id`` and ``fragment_text LIKE %query%``.
        Returns up to *limit* rows ordered by fragment length ascending
        (shorter fragments tend to be more focused / higher quality).
        """
        like = f"%{query}%"
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT f.fragment_id, f.fragment_text, f.fragment_type,
                          us.citekey, p.title, p.authors, p.year
                   FROM fragments f
                   JOIN papers p ON f.paper_id = p.paper_id
                   JOIN user_sources us ON f.paper_id = us.paper_id
                   WHERE us.user_id = ?
                     AND f.fragment_text LIKE ?
                   ORDER BY LENGTH(f.fragment_text) ASC
                   LIMIT ?""",
                (user_id, like, limit),
            ).fetchall()
        return [
            {
                "fragment_id": row["fragment_id"],
                "text": row["fragment_text"],
                "fragment_type": row["fragment_type"] or "key_idea",
                "citekey": row["citekey"],
                "title": row["title"] or "",
                "authors": row["authors"] or "",
                "year": row["year"],
            }
            for row in rows
        ]

    # ---------------------------------------------------------------- #
    # Recommendations cache (LLM-curated library recommendations)       #
    # ---------------------------------------------------------------- #

    def get_cached_recommendations(
        self,
        *,
        user_id: str,
        project_id: str,
        library_state_hash: str,
        outline_hash: str,
        model: str,
    ) -> Optional[dict]:
        """Return the cached recommendations payload or None on miss."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT json_result, created_at, model
                   FROM recommendations_cache
                   WHERE user_id = ? AND project_id = ?
                     AND library_state_hash = ? AND outline_hash = ?
                     AND model = ?""",
                (user_id, project_id, library_state_hash, outline_hash, model),
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def save_cached_recommendations(
        self,
        *,
        user_id: str,
        project_id: str,
        library_state_hash: str,
        outline_hash: str,
        model: str,
        json_result: str,
    ) -> None:
        """Upsert a cached recommendations entry."""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO recommendations_cache
                   (user_id, project_id, library_state_hash, outline_hash,
                    model, json_result, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                (user_id, project_id, library_state_hash, outline_hash,
                 model, json_result),
            )

    def invalidate_recommendations_cache(
        self, user_id: str, project_id: Optional[str] = None
    ) -> int:
        """Drop cached recommendations for a user (optionally scoped by project).

        Called when the library changes (upload/delete) or when a project's
        outline changes. Returns the number of rows removed.
        """
        with self._conn() as conn:
            if project_id is None:
                cur = conn.execute(
                    "DELETE FROM recommendations_cache WHERE user_id = ?",
                    (user_id,),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM recommendations_cache WHERE user_id = ? AND project_id = ?",
                    (user_id, project_id),
                )
        return cur.rowcount


# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #


def _row_to_paper(row: sqlite3.Row, paper_record_cls: type) -> "PaperRecord":
    return paper_record_cls(
        paper_id=row["paper_id"],
        pdf_hash=row["pdf_hash"],
        doi=row["doi"],
        title=row["title"] or "",
        authors=row["authors"] or "",
        year=row["year"],
        abstract=row["abstract"] or "",
    )
