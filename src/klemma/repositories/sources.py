"""Source management repository — CRUD, status, sections, Zotero keys, vault sync."""

from datetime import date, datetime
from typing import Optional

from .base import BaseRepository

# Subquery for filtering out sources marked for pruning
PRUNE_DROP_SUBQUERY = (
    "SELECT source_id FROM prune_verdicts "
    "WHERE verdict='drop' AND updated_at > datetime('now', '-14 days')"
)


class ProcessingStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ALL = [PENDING, PROCESSING, COMPLETED, FAILED, SKIPPED]


class SourceRepository(BaseRepository):

    def register_sources(self, source_ids: list[str]):
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO sources (id) VALUES (?)",
                [(sid,) for sid in source_ids],
            )

    def set_pdf_path(self, source_id: str, path: str):
        """Set the direct PDF path for a source."""
        with self._conn() as conn:
            conn.execute("UPDATE sources SET pdf_path = ? WHERE id = ?", (path, source_id))

    def get_pending_sources(self, limit: int = 0) -> list[str]:
        with self._conn() as conn:
            if limit > 0:
                today = date.today().isoformat()
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
                    (ProcessingStatus.PENDING, remaining),
                )
            else:
                cur = conn.execute(
                    "SELECT id FROM sources WHERE status = ? ORDER BY id",
                    (ProcessingStatus.PENDING,),
                )
            return [row["id"] for row in cur.fetchall()]

    def mark_processing(self, source_id: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE sources SET status = ? WHERE id = ?",
                (ProcessingStatus.PROCESSING, source_id),
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
                    ProcessingStatus.COMPLETED, now, note_path, quality_score,
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
                (ProcessingStatus.FAILED, error, source_id),
            )

    def mark_skipped(self, source_id: str, reason: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE sources SET status=?, error_message=? WHERE id=?",
                (ProcessingStatus.SKIPPED, reason, source_id),
            )

    def get_source(self, source_id: str) -> Optional[dict]:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_existing_source_ids(self) -> set[str]:
        """Return set of all source IDs in DB."""
        with self._conn() as conn:
            return {row["id"] for row in conn.execute("SELECT id FROM sources")}

    def get_sources_without_embeddings(self) -> list[str]:
        """Return citekeys of completed sources missing embeddings."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT id FROM sources WHERE status='completed' AND embedding IS NULL"
            )
            return [row["id"] for row in cur]

    def get_stats(self) -> dict[str, int]:
        with self._conn() as conn:
            stats = {}
            for s in ProcessingStatus.ALL:
                cur = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sources WHERE status=?", (s,)
                )
                stats[s] = cur.fetchone()["cnt"]
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
        """Write all section/chapter assignments for a source."""
        with self._conn() as conn:
            self._set_sections_sql(conn, source_id, sections, chapters)

    @staticmethod
    def _set_sections_sql(conn, source_id: str, sections: list[str], chapters: list[int]):
        """Write source_sections using an existing connection (avoids nested lock)."""
        conn.execute("DELETE FROM source_sections WHERE source_id=?", (source_id,))
        for sec in sections:
            ch = int(sec.split(".")[0]) if "." in sec else None
            if ch is None:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO source_sections (source_id, chapter, section) VALUES (?, ?, ?)",
                (source_id, ch, sec),
            )
        existing_chapters = {int(s.split(".")[0]) for s in sections if "." in s}
        for ch in chapters:
            if ch not in existing_chapters:
                conn.execute(
                    "INSERT OR IGNORE INTO source_sections (source_id, chapter, section) VALUES (?, ?, ?)",
                    (source_id, ch, str(ch)),
                )

    def get_all_sources(self) -> list[dict]:
        """Get all completed sources with metadata."""
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT id, note_path, quality_score, primary_chapter,
                          primary_section, relevance_nr1, relevance_nr2,
                          citation_priority, fragment_count
                   FROM sources WHERE status=?
                   ORDER BY primary_chapter, citation_priority DESC, quality_score DESC""",
                (ProcessingStatus.COMPLETED,),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_by_chapter(self, chapter: int) -> list[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                f"""SELECT DISTINCT s.id, s.note_path, s.quality_score, s.primary_section,
                          s.relevance_nr1, s.relevance_nr2, s.citation_priority,
                          s.fragment_count
                   FROM sources s
                   LEFT JOIN source_sections ss ON s.id = ss.source_id
                   WHERE s.status=? AND (s.primary_chapter=? OR ss.chapter=?)
                     AND s.id NOT IN ({PRUNE_DROP_SUBQUERY})
                   ORDER BY s.citation_priority DESC, s.quality_score DESC""",
                (ProcessingStatus.COMPLETED, chapter, chapter),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_by_section(self, section: str) -> list[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                f"""SELECT DISTINCT s.id, s.note_path, s.quality_score, s.primary_chapter,
                          s.relevance_nr1, s.relevance_nr2, s.citation_priority,
                          s.fragment_count
                   FROM sources s
                   LEFT JOIN source_sections ss ON s.id = ss.source_id
                   WHERE s.status=? AND (s.primary_section LIKE ? OR ss.section LIKE ?)
                     AND s.id NOT IN ({PRUNE_DROP_SUBQUERY})
                   ORDER BY s.citation_priority DESC, s.quality_score DESC""",
                (ProcessingStatus.COMPLETED, f"{section}%", f"{section}%"),
            )
            return [dict(row) for row in cur.fetchall()]

    # ── Zotero Key / Rename ────────────────────────────────────────────────

    def get_zotero_key_map(self) -> dict[str, str]:
        """Return {zotero_key: citekey} for all sources with a populated zotero_key."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT id, zotero_key FROM sources WHERE zotero_key IS NOT NULL AND zotero_key != ''"
            )
            return {row["zotero_key"]: row["id"] for row in cur.fetchall()}

    def rename_source(self, old_id: str, new_id: str, zotero_key: str = ""):
        """Cascade-rename source across all tables."""
        with self._conn() as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute(
                "UPDATE sources SET id=?, zotero_key=? WHERE id=?",
                (new_id, zotero_key, old_id),
            )
            for table, col in [
                ("fragments", "source_id"),
                ("source_sections", "source_id"),
                ("reference_gaps", "source_id"),
                ("reading_queue", "source_id"),
                ("prune_verdicts", "source_id"),
            ]:
                conn.execute(
                    f"UPDATE {table} SET {col}=? WHERE {col}=?",
                    (new_id, old_id),
                )
            conn.execute("PRAGMA foreign_keys=ON")

    def delete_source(self, source_id: str):
        """Delete an orphan source and all its FK-dependent rows."""
        with self._conn() as conn:
            for table, col in [
                ("fragments", "source_id"),
                ("source_sections", "source_id"),
                ("reference_gaps", "source_id"),
                ("reading_queue", "source_id"),
                ("prune_verdicts", "source_id"),
            ]:
                conn.execute(f"DELETE FROM {table} WHERE {col}=?", (source_id,))
            conn.execute("DELETE FROM sources WHERE id=?", (source_id,))

    def populate_zotero_keys(self, mapping: dict[str, str]):
        """Backfill zotero_key from {citekey: itemKey}. Only updates NULL rows."""
        with self._conn() as conn:
            for citekey, item_key in mapping.items():
                conn.execute(
                    "UPDATE sources SET zotero_key=? WHERE id=? AND (zotero_key IS NULL OR zotero_key='')",
                    (item_key, citekey),
                )

    # ── Vault Sync ──────────────────────────────────────────────────────────

    def sync_source_sections(
        self,
        vault_data: list[dict],
        new_entries: list[tuple[str, dict]],
    ) -> dict:
        """Sync section assignments from vault frontmatter and register new Zotero entries.

        vault_data: [{citekey, primary_section, primary_chapter, sections, chapters,
                      quality, priority, nr1, nr2, note_path}]
        new_entries: [(citekey, {chapter, section, chapters, sections})]

        Returns {"vault_updated": int, "new_registered": int, "unchanged": int}.
        """
        vault_updated = 0
        unchanged = 0
        new_registered = 0

        with self._conn() as conn:
            for vd in vault_data:
                citekey = vd["citekey"]

                cur = conn.execute(
                    """SELECT primary_section, primary_chapter, quality_score,
                              relevance_nr1, relevance_nr2, citation_priority
                       FROM sources WHERE id=?""",
                    (citekey,),
                )
                row = cur.fetchone()
                if not row:
                    conn.execute("INSERT OR IGNORE INTO sources (id) VALUES (?)", (citekey,))

                # Current DB sections
                cur = conn.execute(
                    "SELECT section FROM source_sections WHERE source_id=?",
                    (citekey,),
                )
                db_sections = {r["section"] for r in cur.fetchall()}
                vault_sections = set(vd.get("sections", []))

                vault_primary = vd.get("primary_section")
                needs_update = (
                    row is None
                    or vault_primary != (row["primary_section"] or None)
                    or vault_sections != db_sections
                    or (vd.get("quality", 0) or 0) != (row["quality_score"] or 0)
                    or (vd.get("nr1", 0) or 0) != (row["relevance_nr1"] or 0)
                    or (vd.get("nr2", 0) or 0) != (row["relevance_nr2"] or 0)
                )

                if needs_update:
                    conn.execute(
                        """UPDATE sources SET
                            primary_section=?, primary_chapter=?,
                            quality_score=?, relevance_nr1=?, relevance_nr2=?,
                            citation_priority=?, note_path=COALESCE(?, note_path),
                            status=CASE WHEN status='pending' THEN 'completed' ELSE status END
                        WHERE id=?""",
                        (
                            vault_primary, vd.get("primary_chapter"),
                            vd.get("quality", 0), vd.get("nr1", 0), vd.get("nr2", 0),
                            vd.get("priority", "medium"), vd.get("note_path"),
                            citekey,
                        ),
                    )
                    self._set_sections_sql(conn, citekey, list(vault_sections), vd.get("chapters", []))
                    # Auto-restore: if user edited vault note, clear prune verdict
                    conn.execute("DELETE FROM prune_verdicts WHERE source_id=?", (citekey,))
                    vault_updated += 1
                else:
                    unchanged += 1

            # Register new Zotero entries not in DB
            for citekey, classification in new_entries:
                cur = conn.execute("SELECT id FROM sources WHERE id=?", (citekey,))
                if cur.fetchone():
                    continue
                conn.execute(
                    """INSERT INTO sources (id, status, primary_chapter, primary_section)
                       VALUES (?, 'pending', ?, ?)""",
                    (citekey, classification.get("chapter"), classification.get("section")),
                )
                sections = classification.get("sections", [])
                chapters = classification.get("chapters", [])
                if sections:
                    self._set_sections_sql(conn, citekey, sections, chapters)
                new_registered += 1

        return {
            "vault_updated": vault_updated,
            "new_registered": new_registered,
            "unchanged": unchanged,
        }
