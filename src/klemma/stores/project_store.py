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

_SCHEMA_VERSION = 6  # v6: extraction runs + active set + user-scoped project_fragments

_CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_sources (
    citekey          TEXT NOT NULL,
    paper_id         TEXT NOT NULL,
    primary_chapter  INTEGER,
    primary_section  TEXT,
    relevance_nr1    INTEGER DEFAULT 0,
    relevance_nr2    INTEGER DEFAULT 0,
    citation_priority TEXT DEFAULT 'medium',
    added_at         TEXT DEFAULT (datetime('now')),
    user_id          TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, citekey)
);
CREATE INDEX IF NOT EXISTS idx_ps_paper ON project_sources(paper_id);
CREATE INDEX IF NOT EXISTS idx_ps_user ON project_sources(user_id);

CREATE TABLE IF NOT EXISTS project_source_sections (
    citekey      TEXT NOT NULL,
    section      TEXT NOT NULL,
    section_type TEXT,
    chapter      INTEGER,
    user_id      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, citekey, section)
);
CREATE INDEX IF NOT EXISTS idx_pss_section ON project_source_sections(section);

"""

_CREATE_PROJECT_FRAGMENTS = """
CREATE TABLE IF NOT EXISTS project_fragments (
    fragment_id    TEXT NOT NULL,
    citekey        TEXT NOT NULL DEFAULT '',
    user_id        TEXT NOT NULL DEFAULT '',
    section        TEXT,
    section_type   TEXT,
    chapter        INTEGER,
    relevance_score INTEGER DEFAULT 3,
    usage_hint     TEXT,
    used_in_draft  INTEGER DEFAULT 0,
    curated_section TEXT,
    legacy_section  TEXT,
    section_origin  TEXT NOT NULL DEFAULT 'model',
    PRIMARY KEY (user_id, citekey, fragment_id)
);
CREATE INDEX IF NOT EXISTS idx_pf_section ON project_fragments(section);
CREATE INDEX IF NOT EXISTS idx_pf_citekey ON project_fragments(citekey);
CREATE INDEX IF NOT EXISTS idx_pf_fragment ON project_fragments(fragment_id);
"""

_CREATE_SCHEMA = _CREATE_SCHEMA + _CREATE_PROJECT_FRAGMENTS

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS project_extraction_runs (
    run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT NOT NULL DEFAULT '',
    citekey             TEXT NOT NULL,
    paper_id            TEXT,
    attempt_id          TEXT,
    request_fingerprint TEXT,
    started_at          TEXT DEFAULT (datetime('now')),
    finished_at         TEXT,
    mode                TEXT NOT NULL DEFAULT 'standard',
    prompt_name         TEXT,
    prompt_hash         TEXT,
    template_hash       TEXT,
    ai_model            TEXT,
    klemma_version      TEXT,
    extractor_version   TEXT,
    source_content_hash TEXT,
    outline_hash        TEXT,
    config_json         TEXT,
    coverage_json       TEXT,
    is_partial          INTEGER NOT NULL DEFAULT 0,
    validation_incomplete INTEGER NOT NULL DEFAULT 0,
    activation_reason   TEXT,
    chunk_count         INTEGER NOT NULL DEFAULT 0,
    failed_chunks       INTEGER NOT NULL DEFAULT 0,
    fragment_count      INTEGER NOT NULL DEFAULT 0,
    tokens_in           INTEGER NOT NULL DEFAULT 0,
    tokens_out          INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL,
    status              TEXT NOT NULL DEFAULT 'running',
    error               TEXT,
    notes_json          TEXT
);
CREATE INDEX IF NOT EXISTS idx_per_source ON project_extraction_runs(user_id, citekey, run_id);

CREATE TABLE IF NOT EXISTS project_run_fragments (
    run_id          INTEGER NOT NULL REFERENCES project_extraction_runs(run_id),
    fragment_id     TEXT NOT NULL,
    relevance_score INTEGER DEFAULT 3,
    usage_hint      TEXT,
    model_section   TEXT,
    chapter         INTEGER,
    verbatim_status TEXT,
    PRIMARY KEY (run_id, fragment_id)
);
CREATE INDEX IF NOT EXISTS idx_prf_fragment ON project_run_fragments(fragment_id);
"""

RUN_STATUSES = ("running", "pending", "published", "published_partial", "failed", "discarded")
ACTIVE_STATUSES = ("published", "published_partial")

_MIGRATE_V2 = """
CREATE TABLE IF NOT EXISTS prune_verdicts (
    source_id  TEXT NOT NULL,
    verdict    TEXT NOT NULL CHECK(verdict IN ('drop', 'maybe')),
    reason     TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now')),
    user_id    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (user_id, source_id)
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

    @staticmethod
    def _uid(user_id: Optional[str]) -> str:
        """Normalize user_id: None → '' for composite PK compatibility."""
        return user_id if user_id is not None else ""

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            # Fresh DB — create with v4 schema directly
            conn.executescript(_CREATE_SCHEMA)
        else:
            # Ensure all expected columns exist on old DBs (v1 may be minimal)
            for col, typ in [
                ("primary_chapter", "INTEGER"),
                ("primary_section", "TEXT"),
                ("relevance_nr1", "INTEGER DEFAULT 0"),
                ("relevance_nr2", "INTEGER DEFAULT 0"),
                ("citation_priority", "TEXT DEFAULT 'medium'"),
                ("added_at", "TEXT"),
                ("user_id", "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE project_sources ADD COLUMN {col} {typ}")
                except sqlite3.OperationalError:
                    pass  # column already exists
        if version < 2:
            conn.executescript(_MIGRATE_V2)
        if version < 3:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ps_user ON project_sources(user_id)"
            )
        # Ensure junction table exists before v4 migration reads from it
        conn.execute("""CREATE TABLE IF NOT EXISTS project_source_sections (
            citekey TEXT NOT NULL, section TEXT NOT NULL, section_type TEXT,
            chapter INTEGER, PRIMARY KEY (citekey, section))""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pss_section ON project_source_sections(section)")
        # v4: composite PK (user_id, citekey) to prevent cross-user collision.
        # Check actual PK structure for idempotency.
        pk_cols = [
            r[1] for r in conn.execute("PRAGMA table_info(project_sources)").fetchall()
            if r[5] > 0
        ]
        needs_pk_migration = pk_cols == ["citekey"]
        if version < 4 or needs_pk_migration:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS project_sources_v4 (
                    citekey          TEXT NOT NULL,
                    paper_id         TEXT NOT NULL,
                    primary_chapter  INTEGER,
                    primary_section  TEXT,
                    relevance_nr1    INTEGER DEFAULT 0,
                    relevance_nr2    INTEGER DEFAULT 0,
                    citation_priority TEXT DEFAULT 'medium',
                    added_at         TEXT DEFAULT (datetime('now')),
                    user_id          TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (user_id, citekey)
                );
                INSERT OR IGNORE INTO project_sources_v4
                    (citekey, paper_id, primary_chapter, primary_section,
                     relevance_nr1, relevance_nr2, citation_priority, added_at, user_id)
                SELECT citekey, paper_id, primary_chapter, primary_section,
                       relevance_nr1, relevance_nr2, citation_priority, added_at,
                       COALESCE(user_id, '')
                FROM project_sources;

                CREATE TABLE IF NOT EXISTS project_source_sections_v4 (
                    citekey      TEXT NOT NULL,
                    section      TEXT NOT NULL,
                    section_type TEXT,
                    chapter      INTEGER,
                    user_id      TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (user_id, citekey, section)
                );
                INSERT OR IGNORE INTO project_source_sections_v4
                    (citekey, section, section_type, chapter, user_id)
                SELECT pss.citekey, pss.section, pss.section_type, pss.chapter,
                       COALESCE(ps.user_id, '')
                FROM project_source_sections pss
                LEFT JOIN project_sources ps ON ps.citekey = pss.citekey;

                DROP TABLE project_source_sections;
                DROP TABLE project_sources;
                ALTER TABLE project_sources_v4 RENAME TO project_sources;
                ALTER TABLE project_source_sections_v4 RENAME TO project_source_sections;
                CREATE INDEX IF NOT EXISTS idx_ps_paper ON project_sources(paper_id);
                CREATE INDEX IF NOT EXISTS idx_ps_user ON project_sources(user_id);
                CREATE INDEX IF NOT EXISTS idx_pss_section ON project_source_sections(section);
            """)
        # v5: add user_id to prune_verdicts for multi-user isolation.
        # Check actual PK structure for idempotency.
        pv_pk_cols = [
            r[1] for r in conn.execute("PRAGMA table_info(prune_verdicts)").fetchall()
            if r[5] > 0
        ]
        needs_pv_migration = pv_pk_cols == ["source_id"]  # old schema: source_id-only PK
        if version < 5 or needs_pv_migration:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS prune_verdicts_v5 (
                    source_id  TEXT NOT NULL,
                    verdict    TEXT NOT NULL CHECK(verdict IN ('drop', 'maybe')),
                    reason     TEXT DEFAULT '',
                    updated_at TEXT DEFAULT (datetime('now')),
                    user_id    TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (user_id, source_id)
                );
                INSERT OR IGNORE INTO prune_verdicts_v5
                    (source_id, verdict, reason, updated_at, user_id)
                SELECT source_id, verdict, reason, updated_at, ''
                FROM prune_verdicts;
                DROP TABLE prune_verdicts;
                ALTER TABLE prune_verdicts_v5 RENAME TO prune_verdicts;
            """)
        # v6: extraction runs, active set, user-scoped project_fragments (plan C2).
        # project_fragments had PK fragment_id without user_id — two users of one
        # project.db would share curated_section. Rebuilt with PK
        # (user_id, citekey, fragment_id); legacy `section` is kept and copied to
        # legacy_section with section_origin='legacy_unknown' (never guessed).
        pf_pk = [
            r[1] for r in conn.execute("PRAGMA table_info(project_fragments)").fetchall()
            if r[5] > 0
        ]
        needs_pf_migration = pf_pk == ["fragment_id"]
        if version < 6 or needs_pf_migration:
            conn.executescript(_CREATE_RUNS)
            if not pf_pk:  # v1-era DB without project_fragments at all
                conn.executescript(_CREATE_PROJECT_FRAGMENTS)
            if needs_pf_migration:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS project_fragments_v6 (
                        fragment_id    TEXT NOT NULL,
                        citekey        TEXT NOT NULL DEFAULT '',
                        user_id        TEXT NOT NULL DEFAULT '',
                        section        TEXT,
                        section_type   TEXT,
                        chapter        INTEGER,
                        relevance_score INTEGER DEFAULT 3,
                        usage_hint     TEXT,
                        used_in_draft  INTEGER DEFAULT 0,
                        curated_section TEXT,
                        legacy_section  TEXT,
                        section_origin  TEXT NOT NULL DEFAULT 'model',
                        PRIMARY KEY (user_id, citekey, fragment_id)
                    );
                    INSERT OR IGNORE INTO project_fragments_v6
                        (fragment_id, citekey, user_id, section, section_type, chapter,
                         relevance_score, usage_hint, used_in_draft, legacy_section,
                         section_origin)
                    SELECT fragment_id, COALESCE(citekey, ''), '', section, section_type,
                           chapter, relevance_score, usage_hint, used_in_draft, section,
                           'legacy_unknown'
                    FROM project_fragments;
                    DROP TABLE project_fragments;
                    ALTER TABLE project_fragments_v6 RENAME TO project_fragments;
                    CREATE INDEX IF NOT EXISTS idx_pf_section ON project_fragments(section);
                    CREATE INDEX IF NOT EXISTS idx_pf_citekey ON project_fragments(citekey);
                    CREATE INDEX IF NOT EXISTS idx_pf_fragment ON project_fragments(fragment_id);
                """)
            ps_cols = {r[1] for r in conn.execute("PRAGMA table_info(project_sources)").fetchall()}
            if "active_run_id" not in ps_cols:
                conn.execute("ALTER TABLE project_sources ADD COLUMN active_run_id INTEGER")
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
        uid = self._uid(user_id)
        primary_section = sections[0] if sections else None
        primary_chapter = chapters[0] if chapters else None
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO project_sources
                   (citekey, paper_id, primary_chapter, primary_section, user_id)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, citekey) DO UPDATE SET
                       paper_id=excluded.paper_id,
                       primary_chapter=excluded.primary_chapter,
                       primary_section=excluded.primary_section""",
                (citekey, paper_id, primary_chapter, primary_section, uid),
            )
            conn.execute(
                "DELETE FROM project_source_sections WHERE citekey = ? AND user_id = ?",
                (citekey, uid),
            )
            conn.executemany(
                """INSERT OR IGNORE INTO project_source_sections
                   (citekey, section, chapter, user_id) VALUES (?, ?, ?, ?)""",
                [
                    (citekey, s, chapters[i] if i < len(chapters) else primary_chapter, uid)
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
                    """SELECT section, COUNT(DISTINCT citekey) as cnt
                       FROM project_source_sections
                       WHERE user_id = ?
                       GROUP BY section ORDER BY section""",
                    (user_id,),
                ).fetchall()
                by_chapter = conn.execute(
                    """SELECT chapter, COUNT(DISTINCT citekey) as cnt
                       FROM project_source_sections
                       WHERE chapter IS NOT NULL AND user_id = ?
                       GROUP BY chapter ORDER BY chapter""",
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

    def remove_source_from_section(
        self, citekey: str, section: str, user_id: Optional[str] = None
    ) -> bool:
        """Remove a single section assignment for *citekey*.

        Optionally checks *user_id* ownership.
        Returns ``True`` if a row was deleted, ``False`` if nothing matched.
        """
        with self._conn() as conn:
            if user_id is not None:
                cursor = conn.execute(
                    "DELETE FROM project_source_sections WHERE citekey = ? AND section = ? AND user_id = ?",
                    (citekey, section, user_id),
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM project_source_sections WHERE citekey = ? AND section = ?",
                    (citekey, section),
                )
        return cursor.rowcount > 0

    def get_source_sections(
        self, citekey: str, user_id: Optional[str] = None
    ) -> list[str]:
        """Return section list for citekey, optionally scoped to a user."""
        with self._conn() as conn:
            if user_id is not None:
                rows = conn.execute(
                    """SELECT section FROM project_source_sections
                       WHERE citekey = ? AND user_id = ?
                       ORDER BY section""",
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
        """Register a fragment assignment to this project (legacy path, user '')."""
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO project_fragments
                   (fragment_id, citekey, user_id, section, section_type, chapter,
                    relevance_score, section_origin)
                   VALUES (?, ?, '', ?, ?, ?, ?, 'model')""",
                (fragment_id, citekey, section, section_type, chapter, relevance_score),
            )


    # ------------------------------------------------------------------ #
    # Extraction runs + active set (plan C2 / ADR-020)                     #
    # ------------------------------------------------------------------ #

    def start_run(self, citekey: str, *, user_id: Optional[str] = None, **fields) -> int:
        """Insert a ``running`` row BEFORE the first AI call; returns run_id.

        ``fields`` are the launch conditions (attempt_id, request_fingerprint,
        paper_id, mode, prompt_*, ai_model, versions, hashes, config_json) —
        duplicated here on purpose so a failed run is reproducible even when
        nothing reached library.db.
        """
        allowed = {
            "paper_id", "attempt_id", "request_fingerprint", "mode", "prompt_name",
            "prompt_hash", "template_hash", "ai_model", "klemma_version",
            "extractor_version", "source_content_hash", "outline_hash", "config_json",
        }
        cols = ["user_id", "citekey"] + [k for k in fields if k in allowed]
        vals = [self._uid(user_id), citekey] + [fields[k] for k in fields if k in allowed]
        with self._conn() as conn:
            cur = conn.execute(
                f"INSERT INTO project_extraction_runs ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})",
                vals,
            )
            return int(cur.lastrowid)

    def fail_run(self, run_id: int, error: str, **counters) -> None:
        self._update_run(run_id, status="failed", error=error, **counters)

    def _update_run(self, run_id: int, **fields) -> None:
        allowed = {
            "status", "error", "coverage_json", "is_partial", "validation_incomplete",
            "chunk_count", "failed_chunks", "fragment_count", "tokens_in", "tokens_out",
            "cost_usd", "notes_json", "activation_reason", "attempt_id",
        }
        sets = [f"{k}=?" for k in fields if k in allowed]
        vals = [fields[k] for k in fields if k in allowed]
        if not sets:
            return
        with self._conn() as conn:
            conn.execute(
                f"UPDATE project_extraction_runs SET {', '.join(sets)}, "
                f"finished_at=datetime('now') WHERE run_id=?",
                vals + [run_id],
            )

    def publish_run(
        self,
        run_id: int,
        fragments: list[dict],
        *,
        is_partial: bool,
        validation_incomplete: bool,
        counters: Optional[dict] = None,
        verify_fragment: Optional[callable] = None,
        replace_legacy: bool = False,
    ) -> str:
        """Step 2 of the publication protocol — ONE transaction in project.db.

        Writes ``project_run_fragments``, upserts ``project_fragments`` (never
        touching ``curated_section``), updates the run row and, only when the
        run is complete and validated, switches ``project_sources.active_run_id``.
        ``verify_fragment(fragment_id) -> bool`` is the cross-database integrity
        check (fragment + attempt link exist in library.db); any failure rolls
        the whole transaction back and marks the run ``failed, error=integrity``.

        Returns the resulting status: ``published`` | ``pending``.
        """
        counters = counters or {}
        with self._conn() as conn:
            run = conn.execute(
                "SELECT * FROM project_extraction_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise ValueError(f"run {run_id} not found")
            uid, citekey = run["user_id"], run["citekey"]
            try:
                if verify_fragment is not None:
                    for fr in fragments:
                        if not verify_fragment(fr["fragment_id"]):
                            raise RuntimeError(f"integrity: {fr['fragment_id']} missing in library")
                for fr in fragments:
                    conn.execute(
                        """INSERT INTO project_run_fragments
                           (run_id, fragment_id, relevance_score, usage_hint, model_section,
                            chapter, verbatim_status)
                           VALUES (?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(run_id, fragment_id) DO UPDATE SET
                             relevance_score=excluded.relevance_score,
                             usage_hint=excluded.usage_hint,
                             model_section=excluded.model_section,
                             chapter=excluded.chapter,
                             verbatim_status=excluded.verbatim_status""",
                        (
                            run_id, fr["fragment_id"], fr.get("relevance_score", 3),
                            fr.get("usage_hint"), fr.get("model_section"),
                            fr.get("chapter"), fr.get("verbatim_status"),
                        ),
                    )
                    conn.execute(
                        """INSERT INTO project_fragments
                           (fragment_id, citekey, user_id, section, section_type, chapter,
                            relevance_score, usage_hint, section_origin)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'model')
                           ON CONFLICT(user_id, citekey, fragment_id) DO UPDATE SET
                             section=excluded.section,
                             chapter=excluded.chapter,
                             relevance_score=excluded.relevance_score,
                             usage_hint=excluded.usage_hint,
                             section_origin=CASE
                               WHEN project_fragments.curated_section IS NOT NULL
                               THEN 'curated' ELSE 'model' END""",
                        (
                            fr["fragment_id"], citekey, uid, fr.get("model_section"),
                            fr.get("section_type"), fr.get("chapter"),
                            fr.get("relevance_score", 3), fr.get("usage_hint"),
                        ),
                    )
                complete = not is_partial and not validation_incomplete
                status = "published" if complete else "pending"
                conn.execute(
                    """UPDATE project_extraction_runs
                       SET status=?, is_partial=?, validation_incomplete=?, fragment_count=?,
                           coverage_json=?, chunk_count=?, failed_chunks=?, tokens_in=?,
                           tokens_out=?, cost_usd=?, notes_json=?, attempt_id=COALESCE(?, attempt_id),
                           finished_at=datetime('now')
                       WHERE run_id=?""",
                    (
                        status, 1 if is_partial else 0, 1 if validation_incomplete else 0,
                        len(fragments), counters.get("coverage_json"),
                        counters.get("chunk_count", 0), counters.get("failed_chunks", 0),
                        counters.get("tokens_in", 0), counters.get("tokens_out", 0),
                        counters.get("cost_usd"), counters.get("notes_json"),
                        counters.get("attempt_id"), run_id,
                    ),
                )
                if complete:
                    if replace_legacy:
                        # --replace: drop project rows not linked to ANY run (legacy);
                        # global library rows are never deleted.
                        conn.execute(
                            """DELETE FROM project_fragments
                               WHERE user_id=? AND citekey=? AND fragment_id NOT IN (
                                 SELECT prf.fragment_id FROM project_run_fragments prf
                                 JOIN project_extraction_runs r ON r.run_id = prf.run_id
                                 WHERE r.user_id=? AND r.citekey=?)""",
                            (uid, citekey, uid, citekey),
                        )
                    self._activate_locked(conn, uid, citekey, run_id)
                return status
            except Exception as exc:
                conn.rollback()
                with self._conn() as c2:
                    c2.execute(
                        "UPDATE project_extraction_runs SET status='failed', error=?, "
                        "finished_at=datetime('now') WHERE run_id=?",
                        (f"integrity: {exc}"[:500], run_id),
                    )
                raise

    def _activate_locked(self, conn, uid: str, citekey: str, run_id: int) -> None:
        row = conn.execute(
            "SELECT status, activation_reason FROM project_extraction_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None or row["status"] not in ACTIVE_STATUSES:
            raise RuntimeError(f"run {run_id} is {row['status'] if row else 'missing'}, not activatable")
        if row["status"] == "published_partial" and not row["activation_reason"]:
            raise RuntimeError("published_partial without activation_reason")
        conn.execute(
            """INSERT INTO project_sources (citekey, paper_id, user_id, active_run_id)
               VALUES (?, COALESCE((SELECT paper_id FROM project_extraction_runs WHERE run_id=?), ''), ?, ?)
               ON CONFLICT(user_id, citekey) DO UPDATE SET active_run_id=excluded.active_run_id""",
            (citekey, run_id, uid, run_id),
        )

    def activate_partial(
        self, run_id: int, reason: str, *, user_id: Optional[str] = None,
    ) -> None:
        """Explicitly activate a pending partial run (``published_partial``)."""
        if not reason.strip():
            raise ValueError("activation reason is required")
        with self._conn() as conn:
            run = conn.execute(
                "SELECT * FROM project_extraction_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise ValueError(f"run {run_id} not found")
            if run["status"] != "pending":
                raise RuntimeError(f"run {run_id} is {run['status']}, only pending can be activated")
            if run["validation_incomplete"]:
                raise RuntimeError("run has validation_incomplete=1 — run `klemma repair --run` first")
            conn.execute(
                "UPDATE project_extraction_runs SET status='published_partial', "
                "activation_reason=? WHERE run_id=?",
                (reason.strip(), run_id),
            )
            self._activate_locked(conn, run["user_id"], run["citekey"], run_id)

    def clear_validation_incomplete(self, run_id: int) -> str:
        """After ``repair --run``: drop the flag; publish automatically when complete."""
        with self._conn() as conn:
            run = conn.execute(
                "SELECT * FROM project_extraction_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise ValueError(f"run {run_id} not found")
            conn.execute(
                "UPDATE project_extraction_runs SET validation_incomplete=0 WHERE run_id=?",
                (run_id,),
            )
            if run["status"] == "pending" and not run["is_partial"]:
                conn.execute(
                    "UPDATE project_extraction_runs SET status='published' WHERE run_id=?",
                    (run_id,),
                )
                self._activate_locked(conn, run["user_id"], run["citekey"], run_id)
                return "published"
            return run["status"]

    def get_stale_running_citekeys(self, older_than_hours: float = 2.0) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT DISTINCT citekey FROM project_extraction_runs
                   WHERE status='running' AND started_at < datetime('now', ?)""",
                (f"-{older_than_hours * 60:.0f} minutes",),
            ).fetchall()
        return [r[0] for r in rows]

    def mark_stale_runs(self, older_than_hours: float = 2.0) -> int:
        """``running`` rows older than the timeout → ``failed, error=stale``."""
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE project_extraction_runs
                   SET status='failed', error='stale', finished_at=datetime('now')
                   WHERE status='running'
                     AND started_at < datetime('now', ?)""",
                (f"-{older_than_hours * 60:.0f} minutes",),
            )
            return cur.rowcount

    def get_run(self, run_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM project_extraction_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_runs(self, citekey: str, user_id: Optional[str] = None) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM project_extraction_runs WHERE user_id=? AND citekey=? ORDER BY run_id",
                (self._uid(user_id), citekey),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_active_run_id(self, citekey: str, user_id: Optional[str] = None) -> Optional[int]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT active_run_id FROM project_sources WHERE user_id=? AND citekey=?",
                (self._uid(user_id), citekey),
            ).fetchone()
        return int(row["active_run_id"]) if row and row["active_run_id"] is not None else None

    def get_run_fragments(self, run_id: int) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM project_run_fragments WHERE run_id=? ORDER BY rowid", (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_project_fragments(
        self,
        citekey: str,
        *,
        user_id: Optional[str] = None,
        run_id: Optional[int] = None,
        all_runs: bool = False,
    ) -> list[dict]:
        """Project-side rows for a source.

        Default = the ACTIVE set: rows linked to ``active_run_id``, or every
        row without run links (legacy) when no run is active. ``run_id`` gives
        that run's snapshot; ``all_runs`` returns every row with a ``run_ids``
        column listing the runs that produced it.
        """
        uid = self._uid(user_id)
        with self._conn() as conn:
            if run_id is not None:
                rows = conn.execute(
                    """SELECT pf.*, prf.model_section AS run_model_section,
                              prf.relevance_score AS run_relevance, prf.usage_hint AS run_usage_hint,
                              prf.verbatim_status
                       FROM project_run_fragments prf
                       JOIN project_fragments pf
                         ON pf.fragment_id = prf.fragment_id AND pf.user_id=? AND pf.citekey=?
                       WHERE prf.run_id=? ORDER BY prf.rowid""",
                    (uid, citekey, run_id),
                ).fetchall()
                return [dict(r) for r in rows]
            if all_runs:
                rows = conn.execute(
                    """SELECT pf.*, (
                         SELECT group_concat(prf.run_id) FROM project_run_fragments prf
                         JOIN project_extraction_runs r ON r.run_id = prf.run_id
                         WHERE prf.fragment_id = pf.fragment_id AND r.user_id=pf.user_id
                           AND r.citekey=pf.citekey) AS run_ids
                       FROM project_fragments pf WHERE pf.user_id=? AND pf.citekey=?
                       ORDER BY pf.rowid""",
                    (uid, citekey),
                ).fetchall()
                return [dict(r) for r in rows]
            active = self.get_active_run_id(citekey, uid)
            if active is None:
                rows = conn.execute(
                    """SELECT pf.* FROM project_fragments pf
                       WHERE pf.user_id=? AND pf.citekey=? AND pf.fragment_id NOT IN (
                         SELECT prf.fragment_id FROM project_run_fragments prf
                         JOIN project_extraction_runs r ON r.run_id = prf.run_id
                         WHERE r.user_id=? AND r.citekey=?)
                       ORDER BY pf.rowid""",
                    (uid, citekey, uid, citekey),
                ).fetchall()
                return [dict(r) for r in rows]
            rows = conn.execute(
                """SELECT pf.*, prf.verbatim_status FROM project_run_fragments prf
                   JOIN project_fragments pf
                     ON pf.fragment_id = prf.fragment_id AND pf.user_id=? AND pf.citekey=?
                   WHERE prf.run_id=? ORDER BY prf.rowid""",
                (uid, citekey, active),
            ).fetchall()
            return [dict(r) for r in rows]

    def set_curated_section(
        self, citekey: str, fragment_id: str, section: Optional[str],
        *, user_id: Optional[str] = None,
    ) -> bool:
        """Human override of a fragment's section; never touched by runs."""
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE project_fragments
                   SET curated_section=?, section_origin=CASE WHEN ? IS NULL THEN
                       CASE WHEN legacy_section IS NOT NULL AND section IS NULL
                            THEN 'legacy_unknown' ELSE 'model' END ELSE 'curated' END
                   WHERE user_id=? AND citekey=? AND fragment_id=?""",
                (section, section, self._uid(user_id), citekey, fragment_id),
            )
            return cur.rowcount > 0

    def upsert_legacy_fragment(
        self, citekey: str, fragment_id: str, *, user_id: Optional[str] = None,
        section: Optional[str] = None, section_type: Optional[str] = None,
        chapter: Optional[int] = None, relevance_score: int = 3,
        usage_hint: Optional[str] = None, used_in_draft: bool = False,
    ) -> None:
        """Migration path: a monolith fragment becomes a project row with
        ``legacy_section`` and ``section_origin='legacy_unknown'``."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO project_fragments
                   (fragment_id, citekey, user_id, section, section_type, chapter,
                    relevance_score, usage_hint, used_in_draft, legacy_section, section_origin)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'legacy_unknown')
                   ON CONFLICT(user_id, citekey, fragment_id) DO UPDATE SET
                     legacy_section=COALESCE(project_fragments.legacy_section, excluded.legacy_section),
                     relevance_score=COALESCE(project_fragments.relevance_score, excluded.relevance_score),
                     usage_hint=COALESCE(project_fragments.usage_hint, excluded.usage_hint)""",
                (
                    fragment_id, citekey, self._uid(user_id), section, section_type, chapter,
                    relevance_score, usage_hint, 1 if used_in_draft else 0, section,
                ),
            )

    def count_project_fragments(self, citekey: Optional[str] = None, user_id: Optional[str] = None) -> int:
        with self._conn() as conn:
            if citekey is None:
                return conn.execute("SELECT COUNT(*) FROM project_fragments").fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM project_fragments WHERE user_id=? AND citekey=?",
                (self._uid(user_id), citekey),
            ).fetchone()[0]

    def referenced_attempt_ids(self) -> set[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT attempt_id FROM project_extraction_runs WHERE attempt_id IS NOT NULL"
            ).fetchall()
        return {r[0] for r in rows}

    def get_sources_by_section(
        self, section: str, user_id: Optional[str] = None
    ) -> list[str]:
        """Return citekeys assigned to a section, optionally scoped to a user."""
        with self._conn() as conn:
            if user_id is not None:
                rows = conn.execute(
                    """SELECT pss.citekey FROM project_source_sections pss
                       WHERE pss.section = ? AND pss.user_id = ?
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

    def save_prune_verdicts(
        self, drop: list[dict], maybe: list[dict], user_id: Optional[str] = None
    ) -> None:
        """Replace all prune verdicts for a user with fresh results."""
        uid = self._uid(user_id)
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM prune_verdicts WHERE user_id = ?", (uid,)
            )
            for item in drop:
                ck = item.get("citekey", "").lstrip("@")
                if not ck:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO prune_verdicts (source_id, verdict, reason, user_id)"
                    " VALUES (?, 'drop', ?, ?)",
                    (ck, item.get("reason", ""), uid),
                )
            for item in maybe:
                ck = item.get("citekey", "").lstrip("@")
                if not ck:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO prune_verdicts (source_id, verdict, reason, user_id)"
                    " VALUES (?, 'maybe', ?, ?)",
                    (ck, item.get("reason", ""), uid),
                )

    def get_prune_verdicts(
        self,
        verdict: str | None = None,
        chapter: int | None = None,
        section_type: str | None = None,
        user_id: Optional[str] = None,
    ) -> list[dict]:
        """Return prune verdicts, optionally filtered by verdict type and user."""
        uid = self._uid(user_id)
        with self._conn() as conn:
            conditions = [
                f"pv.updated_at > datetime('now', '-{_PRUNE_EXPIRY_DAYS} days')"
            ]
            params: list = []

            if user_id is not None:
                conditions.append("pv.user_id = ?")
                params.append(uid)

            if verdict:
                conditions.append("pv.verdict = ?")
                params.append(verdict)

            if chapter is not None:
                ch = str(chapter)
                conditions.append(
                    "EXISTS (SELECT 1 FROM project_source_sections pss"
                    " WHERE pss.citekey = pv.source_id"
                    " AND pss.user_id = pv.user_id"
                    " AND (pss.section = ? OR pss.section LIKE ?))"
                )
                params.extend([ch, f"{ch}.%"])

            if section_type:
                conditions.append(
                    "EXISTS (SELECT 1 FROM project_source_sections pss2"
                    " WHERE pss2.citekey = pv.source_id"
                    " AND pss2.user_id = pv.user_id"
                    " AND pss2.section_type = ?)"
                )
                params.append(section_type)

            where = " AND ".join(conditions)
            cur = conn.execute(
                f"SELECT pv.source_id, pv.verdict, pv.reason,"
                f" GROUP_CONCAT(DISTINCT pss3.section) as sections"
                f" FROM prune_verdicts pv"
                f" LEFT JOIN project_source_sections pss3"
                f"   ON pss3.citekey = pv.source_id AND pss3.user_id = pv.user_id"
                f" WHERE {where}"
                f" GROUP BY pv.user_id, pv.source_id"
                f" ORDER BY pv.verdict, pv.source_id",
                params,
            )
            return [dict(row) for row in cur.fetchall()]

    def get_prune_drop_ids(
        self, max_age_days: int = _PRUNE_EXPIRY_DAYS, user_id: Optional[str] = None
    ) -> set[str]:
        """Return citekeys with verdict='drop' within expiry window."""
        with self._conn() as conn:
            if user_id is not None:
                cur = conn.execute(
                    "SELECT source_id FROM prune_verdicts"
                    " WHERE verdict='drop' AND updated_at > datetime('now', ?)"
                    " AND user_id = ?",
                    (f"-{max_age_days} days", self._uid(user_id)),
                )
            else:
                cur = conn.execute(
                    "SELECT source_id FROM prune_verdicts"
                    " WHERE verdict='drop' AND updated_at > datetime('now', ?)",
                    (f"-{max_age_days} days",),
                )
            return {row["source_id"] for row in cur.fetchall()}

    def get_prune_summary(self, user_id: Optional[str] = None) -> dict:
        """Return prune verdict counts, optionally scoped to a user."""
        with self._conn() as conn:
            if user_id is not None:
                cur = conn.execute(
                    "SELECT verdict, COUNT(*) as cnt FROM prune_verdicts"
                    " WHERE updated_at > datetime('now', ?) AND user_id = ?"
                    " GROUP BY verdict",
                    (f"-{_PRUNE_EXPIRY_DAYS} days", self._uid(user_id)),
                )
            else:
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

    def clear_prune_verdict(
        self, source_id: str, user_id: Optional[str] = None
    ) -> None:
        """Remove prune verdict for a source, optionally scoped to a user."""
        with self._conn() as conn:
            if user_id is not None:
                conn.execute(
                    "DELETE FROM prune_verdicts WHERE source_id=? AND user_id=?",
                    (source_id, self._uid(user_id)),
                )
            else:
                conn.execute(
                    "DELETE FROM prune_verdicts WHERE source_id=?", (source_id,)
                )

    def get_source_sections_bulk(
        self, citekeys: list[str], user_id: Optional[str] = None
    ) -> dict[str, list[str]]:
        """Return {citekey: [section, ...]} for all given citekeys in one query.

        Used by list_reference_gaps to map citing citekeys to sections without
        N+1 queries.
        """
        if not citekeys:
            return {}
        uid = self._uid(user_id)
        placeholders = ",".join("?" for _ in citekeys)
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT citekey, section
                    FROM project_source_sections
                    WHERE citekey IN ({placeholders}) AND user_id = ?
                    ORDER BY citekey, section""",
                (*citekeys, uid),
            ).fetchall()
        result: dict[str, list[str]] = {ck: [] for ck in citekeys}
        for row in rows:
            result[row["citekey"]].append(row["section"])
        return result

    def get_section_centroids(
        self,
        user_id: str,
        all_user_embeddings: dict[str, list[float]],
        all_user_paper_id_to_citekey: dict[str, str],
    ) -> dict[str, list[float]]:
        """Compute per-section centroid embeddings from user paper embeddings.

        For each section assigned to the user's sources, averages the embeddings
        of all papers assigned to that section. Uses ALL user paper embeddings
        (not just citing ones) for a comprehensive centroid.

        Args:
            user_id: The user whose section assignments to read.
            all_user_embeddings: {paper_id: embedding_vector} for all user papers.
            all_user_paper_id_to_citekey: {paper_id: citekey} for all user papers.

        Returns:
            {section: centroid_vector} — only sections with at least one embedded paper.
        """
        if not all_user_embeddings:
            return {}

        # Build citekey → paper_id reverse map
        citekey_to_paper_id = {v: k for k, v in all_user_paper_id_to_citekey.items()}

        # Get all section assignments for this user's citekeys
        uid = self._uid(user_id)
        citekeys = list(citekey_to_paper_id.keys())
        if not citekeys:
            return {}

        section_vectors: dict[str, list[list[float]]] = {}
        placeholders = ",".join("?" for _ in citekeys)
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT citekey, section
                    FROM project_source_sections
                    WHERE citekey IN ({placeholders}) AND user_id = ?""",
                (*citekeys, uid),
            ).fetchall()

        for row in rows:
            ck = row["citekey"]
            section = row["section"]
            paper_id = citekey_to_paper_id.get(ck)
            if paper_id and paper_id in all_user_embeddings:
                section_vectors.setdefault(section, []).append(
                    all_user_embeddings[paper_id]
                )

        # Compute centroids
        centroids: dict[str, list[float]] = {}
        for section, vectors in section_vectors.items():
            if not vectors:
                continue
            dim = len(vectors[0])
            # Discard vectors with a different dimension — different embedding models
            # stored in the same table can have mismatched dims and cause IndexError.
            consistent = [v for v in vectors if len(v) == dim]
            if not consistent:
                continue
            centroid = [0.0] * dim
            for vec in consistent:
                for i, v in enumerate(vec):
                    centroid[i] += v
            n = len(consistent)
            centroids[section] = [x / n for x in centroid]
        return centroids
