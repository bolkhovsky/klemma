"""LocalPaperStore — SQLite implementation of the PaperStore protocol (ADR-014).

Stores papers, fragments, and embeddings in ~/.klemma/library.db, shared across
all local projects. Enables deduplication: a paper processed once is available
to all projects without re-calling Claude or the embedding API.

Phase 1B: paper_id is a UUID assigned on first register_paper() call.
Phase 1C: LocalUserLibrary will map citekey → paper_id; for now callers that
need citekey-based lookup use find_paper(pdf_hash=...) or find_paper(doi=...).
"""

from __future__ import annotations

import sqlite3
import struct
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator, Optional

if TYPE_CHECKING:
    from ..models import FragmentRecord, PaperRecord

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
"""


class LocalPaperStore:
    """SQLite-backed PaperStore at ~/.klemma/library.db.

    Content-addressable: same PDF → same paper_id → same fragments (dedup).
    Implements the PaperStore protocol from protocols.py.

    Usage::

        store = LocalPaperStore(Path.home() / ".klemma" / "library.db")
        paper_id = store.register_paper(title="...", pdf_hash="abc123", ...)
        frags = store.get_fragments(paper_id)
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
        if version < 1:
            conn.executescript(_CREATE_SCHEMA)
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

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
                    "SELECT * FROM papers WHERE pdf_hash = ?", (pdf_hash,)
                ).fetchone()
                if row:
                    return _row_to_paper(row, PaperRecord)
            if doi:
                row = conn.execute(
                    "SELECT * FROM papers WHERE doi = ?", (doi,)
                ).fetchone()
                if row:
                    return _row_to_paper(row, PaperRecord)
        return None

    def get_paper_by_id(self, paper_id: str) -> Optional["PaperRecord"]:
        """Look up a paper by its paper_id. Returns None if not found."""
        from ..models import PaperRecord

        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM papers WHERE paper_id = ?", (paper_id,)
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

    # ------------------------------------------------------------------ #
    # Fragments                                                            #
    # ------------------------------------------------------------------ #

    def delete_fragments(self, paper_id: str) -> int:
        """Delete all fragments and extractions for paper_id. Returns count deleted."""
        with self._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM fragments WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
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
                        fragment_text, fragment_type, page_number, citation_intent)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f.fragment_id,
                        paper_id,
                        extraction_id,
                        f.fragment_text,
                        f.fragment_type,
                        f.page_number,
                        f.citation_intent,
                    ),
                )
                inserted += cur.rowcount
        return inserted

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
        """Upsert a fragment-level embedding."""
        blob = struct.pack(f"{len(vector)}f", *vector)
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO fragment_embeddings
                   (fragment_id, model_name, vector, dimensions)
                   VALUES (?, ?, ?, ?)""",
                (fragment_id, model, blob, len(vector)),
            )


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
