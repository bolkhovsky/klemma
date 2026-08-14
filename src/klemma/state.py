"""Unified SQLite state manager — facade over domain repositories."""

import sqlite3
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Optional

from .repositories import (
    BenchmarkRepository,
    CitationsRepository,
    ClaimsRepository,
    DecisionsRepository,
    EmbeddingsStoreRepository,
    FragmentRepository,
    GapsRepository,
    PlansRepository,
    PruneRepository,
    SourceRepository,
)


class ProcessingStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    # Completed with defects: some pipeline step (embeddings, sidecar) failed
    # silently; the failed steps are listed in sources.degraded_steps.
    DEGRADED = "degraded"


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
    used_in_draft BOOLEAN DEFAULT 0,
    verbatim INTEGER NOT NULL DEFAULT 0
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

CREATE TABLE IF NOT EXISTS prune_verdicts (
    source_id TEXT PRIMARY KEY REFERENCES sources(id),
    verdict TEXT NOT NULL,
    reason TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manuscript_path TEXT NOT NULL,
    claim_hash TEXT NOT NULL,
    anchor_key TEXT NOT NULL DEFAULT '',
    sentence TEXT NOT NULL,
    citekey TEXT NOT NULL DEFAULT '',
    ref_number INTEGER,
    location TEXT,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    anchor_kind TEXT,
    anchor_raw TEXT,
    verdict TEXT,
    reason TEXT,
    ai_used INTEGER NOT NULL DEFAULT 0,
    judge_model TEXT,
    evidence_start INTEGER,
    evidence_end INTEGER,
    evidence_locator TEXT,
    verified_at TEXT,
    stale INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(manuscript_path, claim_hash, anchor_key)
);
CREATE INDEX IF NOT EXISTS idx_claims_manuscript ON claims(manuscript_path, stale);
"""

PRUNE_EXPIRY_DAYS = 14
PRUNE_DROP_SUBQUERY = (
    "SELECT source_id FROM prune_verdicts "
    "WHERE verdict='drop' AND updated_at > datetime('now', '-14 days')"
)


class StateManager:
    """Facade over domain repositories — backward-compatible public API.

    Repositories are exposed as attributes for direct access:
        state.sources, state.fragments, state.embeddings_store,
        state.gaps, state.citations, state.plans, state.prune

    All original public methods still work via delegation.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

        # Compose domain repositories
        self.sources = SourceRepository(self._conn)
        self.fragments = FragmentRepository(self._conn)
        self.embeddings_store = EmbeddingsStoreRepository(self._conn)
        self.gaps = GapsRepository(self._conn)
        self.citations = CitationsRepository(self._conn)
        self.plans = PlansRepository(self._conn)
        self.prune = PruneRepository(self._conn)
        self.benchmarks = BenchmarkRepository(self._conn)
        self.decisions = DecisionsRepository(self._conn)
        self.claims = ClaimsRepository(self._conn)

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
            self._migrate_schema(conn)

    def _migrate_schema(self, conn):
        """Idempotent schema migrations using PRAGMA user_version.

        Each version bump adds new columns/tables without breaking existing data.
        Runs on every DB open — fast (single PRAGMA check) and safe.
        """
        # NOTE: This chain is specific to the project-local klemma.db.
        # library.db (stores/paper_store.py) has its own independent
        # PRAGMA user_version chain — do not merge the two sequences.
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        target = 16  # bump this when adding new migrations

        if version < 1:
            existing_frag = {
                row[1] for row in conn.execute("PRAGMA table_info(fragments)")
            }
            if "citation_intent" not in existing_frag:
                conn.execute(
                    "ALTER TABLE fragments ADD COLUMN citation_intent TEXT"
                )
            existing_gaps = {
                row[1]
                for row in conn.execute("PRAGMA table_info(reference_gaps)")
            }
            if "citation_intent" not in existing_gaps:
                conn.execute(
                    "ALTER TABLE reference_gaps ADD COLUMN citation_intent TEXT"
                )

        if version < 2:
            existing_src = {
                row[1] for row in conn.execute("PRAGMA table_info(sources)")
            }
            if "embedding" not in existing_src:
                conn.execute(
                    "ALTER TABLE sources ADD COLUMN embedding BLOB"
                )
            if "embedding_model" not in existing_src:
                conn.execute(
                    "ALTER TABLE sources ADD COLUMN embedding_model TEXT"
                )

        if version < 3:
            conn.execute("""CREATE TABLE IF NOT EXISTS citation_links (
                source_id TEXT NOT NULL,
                target_citekey TEXT,
                target_title_hash TEXT NOT NULL,
                target_title TEXT NOT NULL,
                target_authors TEXT,
                target_year INTEGER,
                citation_intent TEXT,
                in_library BOOLEAN DEFAULT 0,
                UNIQUE(source_id, target_title_hash)
            )""")

        if version < 4:
            conn.execute("""CREATE TABLE IF NOT EXISTS benchmark_runs (
                run_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                dataset_path TEXT,
                dataset_hash TEXT,
                metrics_filter TEXT,
                ai_backend TEXT,
                ai_model TEXT,
                results TEXT,
                results_summary TEXT,
                paper_citekey TEXT,
                duration_seconds REAL DEFAULT 0,
                git_commit TEXT,
                klemma_version TEXT,
                config_snapshot TEXT
            )""")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_benchmark_runs_ts "
                "ON benchmark_runs(timestamp DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_benchmark_runs_paper "
                "ON benchmark_runs(paper_citekey)"
            )

        if version < 5:
            existing_frag = {
                row[1] for row in conn.execute("PRAGMA table_info(fragments)")
            }
            if "embedding" not in existing_frag:
                conn.execute(
                    "ALTER TABLE fragments ADD COLUMN embedding BLOB"
                )
            if "embedding_model" not in existing_frag:
                conn.execute(
                    "ALTER TABLE fragments ADD COLUMN embedding_model TEXT"
                )

        if version < 6:
            existing_src = {
                row[1] for row in conn.execute("PRAGMA table_info(sources)")
            }
            for col, col_type in [
                ("title", "TEXT"),
                ("authors", "TEXT"),
                ("year", "INTEGER"),
                ("abstract", "TEXT"),
                ("doi", "TEXT"),
            ]:
                if col not in existing_src:
                    conn.execute(
                        f"ALTER TABLE sources ADD COLUMN {col} {col_type}"
                    )

        if version < 7:
            # Add section_type columns to existing tables
            existing_ss = {
                row[1] for row in conn.execute("PRAGMA table_info(source_sections)")
            }
            if "section_type" not in existing_ss:
                conn.execute(
                    "ALTER TABLE source_sections ADD COLUMN section_type TEXT"
                )

            existing_frag = {
                row[1] for row in conn.execute("PRAGMA table_info(fragments)")
            }
            if "section_type" not in existing_frag:
                conn.execute(
                    "ALTER TABLE fragments ADD COLUMN section_type TEXT"
                )

            existing_gaps = {
                row[1] for row in conn.execute("PRAGMA table_info(reference_gaps)")
            }
            if "section_type" not in existing_gaps:
                conn.execute(
                    "ALTER TABLE reference_gaps ADD COLUMN section_type TEXT"
                )

            # Lookup table: numeric section → semantic type
            conn.execute("""CREATE TABLE IF NOT EXISTS section_type_map (
                section TEXT PRIMARY KEY,
                section_type TEXT NOT NULL,
                chapter INTEGER
            )""")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_stm_type "
                "ON section_type_map(section_type)"
            )

        if version < 8:
            existing_src = {
                row[1] for row in conn.execute("PRAGMA table_info(sources)")
            }
            if "source_role" not in existing_src:
                conn.execute(
                    "ALTER TABLE sources ADD COLUMN source_role TEXT DEFAULT 'external'"
                )

        if version < 9:
            conn.execute("""CREATE TABLE IF NOT EXISTS section_embeddings (
                section TEXT NOT NULL,
                embedding BLOB NOT NULL,
                embedding_model TEXT NOT NULL,
                source_count INTEGER DEFAULT 0,
                updated_at TEXT,
                PRIMARY KEY (section, embedding_model)
            )""")

        if version < 10:
            import logging
            _log = logging.getLogger("klemma.state")

            # 10a. Dedup fragments: keep lowest id per (source_id, fragment_text)
            cur = conn.execute(
                """DELETE FROM fragments WHERE id NOT IN (
                    SELECT MIN(id) FROM fragments
                    GROUP BY source_id, fragment_text
                )"""
            )
            if cur.rowcount:
                _log.info("v10 migration: removed %d duplicate fragments", cur.rowcount)

            # 10b. Rebuild fragments table with UNIQUE(source_id, fragment_text)
            conn.executescript("""
                CREATE TABLE fragments_new (
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
                    used_in_draft BOOLEAN DEFAULT 0,
                    citation_intent TEXT,
                    embedding BLOB,
                    embedding_model TEXT,
                    section_type TEXT,
                    UNIQUE(source_id, fragment_text)
                );

                INSERT OR IGNORE INTO fragments_new
                    (id, source_id, fragment_text, fragment_type, chapter, section,
                     relevance_score, usage_hint, page_number, extracted_at,
                     used_in_draft, citation_intent, embedding, embedding_model,
                     section_type)
                SELECT id, source_id, fragment_text, fragment_type, chapter, section,
                       relevance_score, usage_hint, page_number, extracted_at,
                       used_in_draft, citation_intent, embedding, embedding_model,
                       section_type
                FROM fragments;

                DROP TABLE fragments;
                ALTER TABLE fragments_new RENAME TO fragments;

                CREATE INDEX IF NOT EXISTS idx_fragments_source ON fragments(source_id);
                CREATE INDEX IF NOT EXISTS idx_fragments_section ON fragments(section);
                CREATE INDEX IF NOT EXISTS idx_fragments_type ON fragments(fragment_type);
            """)

            # 10c. Create reassign_skips table
            conn.execute("""CREATE TABLE IF NOT EXISTS reassign_skips (
                source_id TEXT NOT NULL,
                from_section TEXT NOT NULL,
                to_section TEXT NOT NULL,
                skipped_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (source_id, from_section, to_section)
            )""")

        if version < 11:
            import logging
            _log = logging.getLogger("klemma.state")

            # 11: Mark ghost sources (no title AND no authors) as incomplete
            cur = conn.execute(
                """UPDATE sources SET status = 'incomplete'
                   WHERE (title IS NULL OR title = '')
                     AND (authors IS NULL OR authors = '')
                     AND status != 'incomplete'"""
            )
            if cur.rowcount:
                _log.info(
                    "v11 migration: marked %d ghost sources as incomplete",
                    cur.rowcount,
                )

        if version < 12:
            existing_src = {
                row[1] for row in conn.execute("PRAGMA table_info(sources)")
            }
            for col, col_type in [
                ("url", "TEXT"),
                ("source_type", "TEXT DEFAULT 'pdf'"),
            ]:
                if col not in existing_src:
                    conn.execute(
                        f"ALTER TABLE sources ADD COLUMN {col} {col_type}"
                    )

        if version < 13:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    decided_at TEXT,
                    trigger_type TEXT NOT NULL,
                    trigger_source TEXT,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    options_json TEXT NOT NULL DEFAULT '[]',
                    chosen_option TEXT,
                    rationale TEXT,
                    sections TEXT,
                    influenced_by TEXT
                )"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_decisions_pending
                   ON decisions(chosen_option) WHERE chosen_option IS NULL"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_decisions_source
                   ON decisions(trigger_source) WHERE trigger_source IS NOT NULL"""
            )

        if version < 14:
            existing_dec = {
                row[1] for row in conn.execute("PRAGMA table_info(decisions)")
            }
            if "note" not in existing_dec:
                conn.execute("ALTER TABLE decisions ADD COLUMN note TEXT")
            if "feedback" not in existing_dec:
                conn.execute("ALTER TABLE decisions ADD COLUMN feedback TEXT")

        if version < 15:
            existing_frag = {
                row[1] for row in conn.execute("PRAGMA table_info(fragments)")
            }
            if "verbatim" not in existing_frag:
                conn.execute(
                    "ALTER TABLE fragments ADD COLUMN verbatim INTEGER NOT NULL DEFAULT 0"
                )

        if version < 16:
            # Claim-provenance substrate: fragment spans into the sidecar
            # canonical text + human-readable source locator ("п. 3.4",
            # "табл. 2"). `section` is NOT reused — it holds the
            # dissertation section, not the source's.
            existing_frag = {
                row[1] for row in conn.execute("PRAGMA table_info(fragments)")
            }
            for col, col_type in [
                ("char_start", "INTEGER"),
                ("char_end", "INTEGER"),
                ("source_locator", "TEXT"),
            ]:
                if col not in existing_frag:
                    conn.execute(
                        f"ALTER TABLE fragments ADD COLUMN {col} {col_type}"
                    )

            # JSON array of pipeline steps that silently failed (embeddings
            # down, sidecar write failed, ...) — consumer is the future
            # `degraded` source status.
            existing_src = {
                row[1] for row in conn.execute("PRAGMA table_info(sources)")
            }
            if "degraded_steps" not in existing_src:
                conn.execute(
                    "ALTER TABLE sources ADD COLUMN degraded_steps TEXT"
                )

            conn.execute("""CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manuscript_path TEXT NOT NULL,
                claim_hash TEXT NOT NULL,
                anchor_key TEXT NOT NULL DEFAULT '',
                sentence TEXT NOT NULL,
                citekey TEXT NOT NULL DEFAULT '',
                ref_number INTEGER,
                location TEXT,
                char_start INTEGER NOT NULL,
                char_end INTEGER NOT NULL,
                anchor_kind TEXT,
                anchor_raw TEXT,
                verdict TEXT,
                reason TEXT,
                ai_used INTEGER NOT NULL DEFAULT 0,
                judge_model TEXT,
                evidence_start INTEGER,
                evidence_end INTEGER,
                evidence_locator TEXT,
                verified_at TEXT,
                stale INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(manuscript_path, claim_hash, anchor_key)
            )""")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_claims_manuscript "
                "ON claims(manuscript_path, stale)"
            )

        conn.execute(f"PRAGMA user_version = {target}")

    # ── Source delegation ─────────────────────────────────────────────────

    def register_sources(self, source_ids: list[str]):
        return self.sources.register_sources(source_ids)

    def register_online_source(
        self,
        citekey: str,
        title: str,
        authors: str,
        year: Optional[int],
        url: str,
        abstract: str = "",
    ) -> None:
        return self.sources.register_online_source(
            citekey, title, authors, year, url, abstract
        )

    def set_pdf_path(self, source_id: str, path: str):
        return self.sources.set_pdf_path(source_id, path)

    def set_pdf_text_length(self, source_id: str, length: int):
        return self.sources.set_pdf_text_length(source_id, length)

    def get_pending_sources(self, limit: int = 0) -> list[str]:
        return self.sources.get_pending_sources(limit)

    def get_completed_sources(self) -> list[str]:
        return self.sources.get_completed_sources()

    def delete_fragments(self, source_id: str) -> int:
        return self.fragments.delete_fragments(source_id)

    def mark_processing(self, source_id: str):
        return self.sources.mark_processing(source_id)

    def mark_completed(self, source_id: str, note_path: str, quality_score: int = 0,
                       primary_chapter: Optional[int] = None, primary_section: Optional[str] = None,
                       relevance_nr1: int = 0, relevance_nr2: int = 0, citation_priority: str = "medium"):
        return self.sources.mark_completed(source_id, note_path, quality_score,
                                           primary_chapter, primary_section,
                                           relevance_nr1, relevance_nr2, citation_priority)

    def update_source_metadata(self, source_id: str, quality_score: int = 0,
                               primary_chapter: Optional[int] = None, primary_section: Optional[str] = None,
                               relevance_nr1: int = 0, relevance_nr2: int = 0,
                               citation_priority: str = "medium", note_path: Optional[str] = None,
                               status: str = "completed"):
        return self.sources.update_source_metadata(source_id, quality_score,
                                                   primary_chapter, primary_section,
                                                   relevance_nr1, relevance_nr2,
                                                   citation_priority, note_path, status)

    def mark_failed(self, source_id: str, error: str):
        return self.sources.mark_failed(source_id, error)

    def mark_skipped(self, source_id: str, reason: str):
        return self.sources.mark_skipped(source_id, reason)

    def mark_degraded(self, source_id: str, steps: list[str]):
        return self.sources.mark_degraded(source_id, steps)

    def clear_degraded(self, source_id: str):
        return self.sources.clear_degraded(source_id)

    def get_degraded_sources(self) -> list[dict]:
        return self.sources.get_degraded_sources()

    def get_source(self, source_id: str) -> Optional[dict]:
        return self.sources.get_source(source_id)

    def update_source_info(self, source_id: str, title: str = "", authors: str = "",
                           year: Optional[int] = None, abstract: str = "", doi: str = "",
                           url: str = "", source_type: str = ""):
        return self.sources.update_source_info(
            source_id, title, authors, year, abstract, doi, url, source_type
        )

    def set_source_role(self, source_id: str, role: str):
        return self.sources.set_source_role(source_id, role)

    def get_author_publication_counts(self) -> dict[str, int]:
        return self.sources.get_author_publication_counts()

    def get_existing_source_ids(self) -> set[str]:
        return self.sources.get_existing_source_ids()

    def get_sources_missing_title(self) -> list[str]:
        return self.sources.get_sources_missing_title()

    def get_sources_without_embeddings(self) -> list[str]:
        return self.sources.get_sources_without_embeddings()

    def get_sources_with_stale_model(self, current_model: str) -> list[str]:
        return self.sources.get_sources_with_stale_model(current_model)

    def get_stats(self) -> dict[str, int]:
        return self.sources.get_stats()

    def set_source_sections(self, source_id: str, sections: list[str], chapters: list[int]) -> None:
        return self.sources.set_source_sections(source_id, sections, chapters)

    def get_all_sources(self) -> list[dict]:
        return self.sources.get_all_sources()

    def get_all_sources_metadata(self) -> list[dict]:
        return self.sources.get_all_sources_metadata()

    def get_by_chapter(self, chapter: int) -> list[dict]:
        return self.sources.get_by_chapter(chapter)

    def get_by_section(
        self, section: str, section_type: str | None = None,
    ) -> list[dict]:
        return self.sources.get_by_section(section, section_type)

    def get_zotero_key_map(self) -> dict[str, str]:
        return self.sources.get_zotero_key_map()

    def rename_source(self, old_id: str, new_id: str, zotero_key: str = ""):
        return self.sources.rename_source(old_id, new_id, zotero_key)

    def delete_source(self, source_id: str):
        return self.sources.delete_source(source_id)

    def populate_zotero_keys(self, mapping: dict[str, str]):
        return self.sources.populate_zotero_keys(mapping)

    def sync_source_sections(self, vault_data: list[dict], new_entries: list[tuple[str, dict]]) -> dict:
        return self.sources.sync_source_sections(vault_data, new_entries)

    # ── Fragment delegation ───────────────────────────────────────────────

    def save_fragments(self, source_id: str, fragments: list[dict]) -> int:
        return self.fragments.save_fragments(source_id, fragments)

    def get_fragments(self, source_id: Optional[str] = None, chapter: Optional[int] = None,
                      section: Optional[str] = None, fragment_type: Optional[str] = None,
                      limit: int = 50, section_type: str | None = None) -> list[dict]:
        return self.fragments.get_fragments(
            source_id, chapter, section, fragment_type, limit, section_type,
        )

    def get_fragment_stats(self) -> dict:
        return self.fragments.get_fragment_stats()

    def get_intent_coverage(self) -> dict[str, dict[str, int]]:
        return self.fragments.get_intent_coverage()

    def save_fragment_embedding(self, fragment_id: int, embedding: list[float], model: str):
        return self.fragments.save_fragment_embedding(fragment_id, embedding, model)

    def get_fragment_embeddings(self, model: Optional[str] = None) -> dict[int, list[float]]:
        return self.fragments.get_fragment_embeddings(model)

    def get_embedded_fragment_metadata(self, model: Optional[str] = None) -> list[dict]:
        return self.fragments.get_embedded_fragment_metadata(model)

    def update_fragment_section(self, fragment_id: int, section: str) -> bool:
        return self.fragments.update_fragment_section(fragment_id, section)

    def update_fragment_provenance(
        self,
        fragment_id: int,
        *,
        verbatim: Optional[bool] = None,
        char_start: Optional[int] = None,
        char_end: Optional[int] = None,
        source_locator: Optional[str] = None,
    ) -> bool:
        return self.fragments.update_fragment_provenance(
            fragment_id,
            verbatim=verbatim,
            char_start=char_start,
            char_end=char_end,
            source_locator=source_locator,
        )

    def get_fragment_embedding_stats(self) -> dict:
        return self.fragments.get_fragment_embedding_stats()

    def get_unembedded_fragments(self, limit: int = 100000) -> list[dict]:
        return self.fragments.get_unembedded_fragments(limit)

    def get_fragments_with_stale_model(
        self, current_model: str, limit: int = 100000,
    ) -> list[dict]:
        return self.fragments.get_fragments_with_stale_model(current_model, limit)

    def save_reassign_skip(self, source_id: str, from_section: str, to_section: str):
        return self.fragments.save_reassign_skip(source_id, from_section, to_section)

    def save_reassign_skips_batch(self, skips: list[tuple[str, str, str]]) -> int:
        return self.fragments.save_reassign_skips_batch(skips)

    def get_reassign_skips(self) -> set[tuple[str, str, str]]:
        return self.fragments.get_reassign_skips()

    def clear_reassign_skips(self) -> int:
        return self.fragments.clear_reassign_skips()

    def retrieve_similar_fragments(
        self, query_embedding: list[float], top_k: int = 10, model: Optional[str] = None
    ) -> list[dict]:
        return self.fragments.retrieve_similar_fragments(query_embedding, top_k, model)

    # ── Embedding delegation ──────────────────────────────────────────────

    def save_embedding(self, source_id: str, embedding: list[float], model: str):
        return self.embeddings_store.save_embedding(source_id, embedding, model)

    def get_embedding(self, source_id: str) -> Optional[tuple[list[float], str]]:
        return self.embeddings_store.get_embedding(source_id)

    def get_all_embeddings(self, model: Optional[str] = None) -> dict[str, list[float]]:
        return self.embeddings_store.get_all_embeddings(model)

    def get_embedding_stats(self) -> dict:
        return self.embeddings_store.get_embedding_stats()

    def save_section_embedding(self, section: str, embedding: list[float],
                               model: str, source_count: int):
        return self.embeddings_store.save_section_embedding(section, embedding, model, source_count)

    def get_section_embedding(self, section: str, model: Optional[str] = None):
        return self.embeddings_store.get_section_embedding(section, model)

    def get_all_section_embeddings(self, model: Optional[str] = None):
        return self.embeddings_store.get_all_section_embeddings(model)

    def get_section_embedding_stats(self):
        return self.embeddings_store.get_section_embedding_stats()

    # ── Gaps delegation ───────────────────────────────────────────────────

    def save_reference_gaps(self, source_id: str, gaps: list[dict]) -> int:
        return self.gaps.save_reference_gaps(source_id, gaps)

    def get_reference_gaps(
        self,
        section: Optional[str] = None,
        limit: int = 50,
        section_weights: Optional[dict[str, float]] = None,
    ) -> list[dict]:
        return self.gaps.get_reference_gaps(section, limit, section_weights)

    def rerank_gaps_semantic(self, gaps: list[dict], embeddings=None,
                             query_section: Optional[str] = None) -> list[dict]:
        return self.gaps.rerank_gaps_semantic(
            gaps, embeddings, query_section,
            get_all_embeddings=self.embeddings_store.get_all_embeddings,
            get_section_sources=self.gaps.get_section_sources,
        )

    def get_section_sources(
        self, section: str, section_type: str | None = None,
    ) -> list[str]:
        return self.gaps.get_section_sources(section, section_type)

    def get_gap_summary(self) -> dict:
        return self.gaps.get_gap_summary()

    def resolve_gaps(self, entry_lookup: dict) -> int:
        return self.gaps.resolve_gaps(entry_lookup)

    def get_coverage_stats(self) -> dict:
        return self.gaps.get_coverage_stats()

    def get_gaps(self, min_sources: int = 3) -> list[dict]:
        return self.gaps.get_gaps(min_sources)

    def reset_non_completed(self) -> dict[str, int]:
        return self.gaps.reset_non_completed()

    # ── Citation delegation ───────────────────────────────────────────────

    def save_citation_links(self, source_id: str, references: list[dict]):
        return self.citations.save_citation_links(source_id, references)

    def get_citation_links(self, source_id: Optional[str] = None) -> list[dict]:
        return self.citations.get_citation_links(source_id)

    def get_citation_graph_stats(self) -> dict:
        return self.citations.get_citation_graph_stats()

    def get_co_cited(self, citekey: str) -> list[dict]:
        return self.citations.get_co_cited(citekey)

    def get_key_author_groups(self, min_papers: int = 2) -> list[dict]:
        return self.citations.get_key_author_groups(min_papers)

    def get_benchmark_candidates(self) -> list[dict]:
        return self.citations.get_benchmark_candidates()

    # ── Plans delegation ──────────────────────────────────────────────────

    def save_plan(self, dissertation_task: str, assistant_task: str, reading_target: str = "",
                  reading_snippet: str = "", plan_json: str = "", progress_summary: str = ""):
        return self.plans.save_plan(dissertation_task, assistant_task, reading_target,
                                    reading_snippet, plan_json, progress_summary)

    def get_writing_streak(self) -> dict:
        return self.plans.get_writing_streak()

    def get_plan(self, plan_date: Optional[str] = None) -> Optional[dict]:
        return self.plans.get_plan(plan_date)

    def get_yesterday_plan(self) -> Optional[dict]:
        return self.plans.get_yesterday_plan()

    def add_to_reading_queue(self, source_id: str, priority: int = 50, total_length: int = 0):
        return self.plans.add_to_reading_queue(source_id, priority, total_length)

    def get_next_reading(self) -> Optional[dict]:
        return self.plans.get_next_reading()

    def update_reading_position(self, source_id: str, position: int):
        return self.plans.update_reading_position(source_id, position)

    def complete_reading(self, source_id: str):
        return self.plans.complete_reading(source_id)

    # ── Prune delegation ──────────────────────────────────────────────────

    def save_prune_verdicts(self, drop: list[dict], maybe: list[dict]):
        return self.prune.save_prune_verdicts(drop, maybe)

    def get_prune_drop_ids(self, max_age_days: int = PRUNE_EXPIRY_DAYS) -> set[str]:
        return self.prune.get_prune_drop_ids(max_age_days)

    def get_prune_summary(self) -> dict:
        return self.prune.get_prune_summary()

    def get_prune_verdicts(self, chapter: Optional[int] = None, verdict: Optional[str] = None,
                           section_type: str | None = None) -> list[dict]:
        return self.prune.get_prune_verdicts(chapter, verdict, section_type)

    def clear_prune_verdict(self, source_id: str):
        return self.prune.clear_prune_verdict(source_id)

    # ── Benchmark delegation ────────────────────────────────────────────

    def save_benchmark_run(self, **kwargs) -> str:
        return self.benchmarks.save_run(**kwargs)

    def get_benchmark_runs(self, limit: int = 20, paper_citekey: str = "") -> list[dict]:
        return self.benchmarks.get_runs(limit, paper_citekey)

    def get_benchmark_run(self, run_id: str):
        return self.benchmarks.get_run(run_id)

    def get_latest_benchmark_run(self, paper_citekey: str = ""):
        return self.benchmarks.get_latest_run(paper_citekey)

    def compare_benchmark_runs(self, id_a: str, id_b: str) -> dict:
        return self.benchmarks.compare_runs(id_a, id_b)

    def get_benchmarked_citekeys(self) -> set[str]:
        return self.benchmarks.get_benchmarked_citekeys()

    # ── Claims ledger delegation ────────────────────────────────────────

    def record_claim_check(self, manuscript_path: str, entries: list[dict],
                           judge_model: Optional[str] = None) -> int:
        return self.claims.record_check(manuscript_path, entries, judge_model)

    def mark_claims_stale(self, manuscript_path: str, live_hashes: set[str]) -> int:
        return self.claims.mark_stale(manuscript_path, live_hashes)

    def get_claims(self, manuscript_path: str, include_stale: bool = True) -> list[dict]:
        return self.claims.get_claims(manuscript_path, include_stale)

    def get_claims_status_summary(self, manuscript_path: Optional[str] = None) -> list[dict]:
        return self.claims.get_status_summary(manuscript_path)

    def get_sections_for_type(self, section_type: str) -> list[str]:
        """Return numeric section IDs mapped to a semantic type."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT section FROM section_type_map WHERE section_type = ? ORDER BY section",
                (section_type,),
            )
            return [row["section"] for row in cur.fetchall()]

    def get_available_section_types(self) -> list[str]:
        """Return sorted list of distinct section types in the map."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT DISTINCT section_type FROM section_type_map ORDER BY section_type"
            )
            return [row["section_type"] for row in cur.fetchall()]

    # ── Section type sync ──────────────────────────────────────────────────

    def sync_section_types(self, config) -> dict:
        """Populate section_type_map table and backfill section_type columns.

        1. Read config.section_type_map (explicit) + infer from config.chapters
        2. Populate section_type_map table
        3. Backfill section_type on source_sections, fragments, reference_gaps
        4. Return stats: {updated: N, unmapped: [sections]}

        config: ProjectConfig instance.
        """
        from .section_types import infer_section_type

        # Build complete mapping: explicit config + heuristic from chapter names
        mapping: dict[str, str] = {}
        if config.section_type_map:
            mapping.update(config.section_type_map)

        if config.chapters:
            for ch_num, ch_name in config.chapters.items():
                key = str(ch_num)
                if key not in mapping:
                    inferred = infer_section_type(ch_name)
                    if inferred:
                        mapping[key] = inferred.value

        updated = 0
        unmapped: list[str] = []

        with self._conn() as conn:
            # Populate section_type_map table
            for section, section_type in mapping.items():
                ch = int(section.split(".")[0]) if section[0].isdigit() else None
                conn.execute(
                    "INSERT OR REPLACE INTO section_type_map "
                    "(section, section_type, chapter) VALUES (?, ?, ?)",
                    (section, section_type, ch),
                )

            # Backfill source_sections.section_type
            # Use exact match OR prefix match with '.' separator to prevent
            # chapter "2" matching section "20.x" (false-positive prefix match)
            cur = conn.execute(
                """UPDATE source_sections SET section_type = (
                    SELECT stm.section_type FROM section_type_map stm
                    WHERE source_sections.section = stm.section
                       OR source_sections.section LIKE stm.section || '.%'
                    ORDER BY LENGTH(stm.section) DESC LIMIT 1
                ) WHERE section_type IS NULL"""
            )
            updated += cur.rowcount

            # Backfill fragments.section_type
            cur = conn.execute(
                """UPDATE fragments SET section_type = (
                    SELECT stm.section_type FROM section_type_map stm
                    WHERE fragments.section = stm.section
                       OR fragments.section LIKE stm.section || '.%'
                    ORDER BY LENGTH(stm.section) DESC LIMIT 1
                ) WHERE section_type IS NULL AND section IS NOT NULL"""
            )
            updated += cur.rowcount

            # Backfill reference_gaps.section_type from dissertation_sections JSON
            # Match first element of JSON array against section_type_map
            cur = conn.execute(
                """UPDATE reference_gaps SET section_type = (
                    SELECT stm.section_type FROM section_type_map stm
                    WHERE json_extract(dissertation_sections, '$[0]') = stm.section
                       OR json_extract(dissertation_sections, '$[0]') LIKE stm.section || '.%'
                    ORDER BY LENGTH(stm.section) DESC LIMIT 1
                ) WHERE section_type IS NULL
                  AND dissertation_sections IS NOT NULL
                  AND dissertation_sections != '[]'"""
            )
            updated += cur.rowcount

            cur = conn.execute(
                "SELECT DISTINCT section FROM source_sections "
                "WHERE section_type IS NULL"
            )
            unmapped = [row["section"] for row in cur.fetchall()]

        return {"updated": updated, "unmapped": unmapped}

    # ── Aggregation (cross-repo) ──────────────────────────────────────────

    def get_library_summary(self) -> dict:
        """Comprehensive library summary for AI analysis context."""
        with self._conn() as conn:
            stats = self.sources.get_stats()
            frag_stats = self.fragments.get_fragment_stats()
            cov = self.gaps.get_coverage_stats()
            gap_summary = self.gaps.get_gap_summary()

            cur = conn.execute(
                "SELECT quality_score, COUNT(*) as cnt FROM sources "
                "WHERE status='completed' AND quality_score IS NOT NULL "
                "GROUP BY quality_score ORDER BY quality_score DESC"
            )
            by_quality = {row["quality_score"]: row["cnt"] for row in cur.fetchall()}

            cur = conn.execute(
                "SELECT AVG(quality_score) as avg_q, AVG(fragment_count) as avg_f "
                "FROM sources WHERE status='completed' AND quality_score > 0"
            )
            row = cur.fetchone()
            avg_quality = round(row["avg_q"], 1) if row["avg_q"] else 0
            avg_fragments = round(row["avg_f"], 1) if row["avg_f"] else 0

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
        """Completed sources grouped by quality tier (5 -> 1)."""
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
