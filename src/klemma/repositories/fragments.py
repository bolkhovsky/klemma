"""Fragment repository — CRUD and intent coverage stats."""

import struct
from typing import Optional

from .base import BaseRepository


class FragmentRepository(BaseRepository):

    def save_fragments(self, source_id: str, fragments: list[dict]) -> int:
        """Save extracted fragments and update source fragment count."""
        with self._conn() as conn:
            inserted = 0
            for f in fragments:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO fragments
                       (source_id, fragment_text, fragment_type, chapter, section,
                        relevance_score, usage_hint, page_number, citation_intent)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source_id,
                        f.get("text", ""),
                        f.get("type", "key_idea"),
                        f.get("chapter"),
                        f.get("section"),
                        f.get("relevance", 3),
                        f.get("usage_hint", ""),
                        f.get("page"),
                        f.get("citation_intent"),
                    ),
                )
                inserted += cur.rowcount
            conn.execute(
                "UPDATE sources SET fragment_count=? WHERE id=?",
                (inserted, source_id),
            )
            return inserted

    def get_fragments(
        self,
        source_id: Optional[str] = None,
        chapter: Optional[int] = None,
        section: Optional[str] = None,
        fragment_type: Optional[str] = None,
        limit: int = 50,
        section_type: Optional[str] = None,
    ) -> list[dict]:
        """Query fragments with optional filters."""
        conditions = []
        params: list = []
        if source_id:
            conditions.append("source_id=?")
            params.append(source_id)
        if chapter:
            conditions.append("chapter=?")
            params.append(chapter)
        if section:
            conditions.append("section LIKE ?")
            params.append(f"{section}%")
        if section_type:
            conditions.append("f.section_type=?")
            params.append(section_type)
        if fragment_type:
            conditions.append("fragment_type=?")
            params.append(fragment_type)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)

        with self._conn() as conn:
            cur = conn.execute(
                f"""SELECT f.*, s.id as citekey
                    FROM fragments f
                    JOIN sources s ON f.source_id = s.id
                    WHERE {where}
                    ORDER BY f.relevance_score DESC, f.extracted_at DESC
                    LIMIT ?""",
                params,
            )
            return [dict(row) for row in cur.fetchall()]

    def get_fragment_stats(self) -> dict:
        """Get fragment counts by type, chapter, section."""
        with self._conn() as conn:
            stats: dict = {"total": 0, "by_type": {}, "by_chapter": {}, "by_section": {}}
            cur = conn.execute("SELECT COUNT(*) as cnt FROM fragments")
            stats["total"] = cur.fetchone()["cnt"]

            cur = conn.execute(
                "SELECT fragment_type, COUNT(*) as cnt FROM fragments GROUP BY fragment_type"
            )
            for row in cur.fetchall():
                stats["by_type"][row["fragment_type"] or "unknown"] = row["cnt"]

            cur = conn.execute(
                "SELECT chapter, COUNT(*) as cnt FROM fragments WHERE chapter IS NOT NULL GROUP BY chapter"
            )
            for row in cur.fetchall():
                stats["by_chapter"][row["chapter"]] = row["cnt"]

            cur = conn.execute(
                "SELECT section, COUNT(*) as cnt FROM fragments WHERE section IS NOT NULL GROUP BY section"
            )
            for row in cur.fetchall():
                stats["by_section"][row["section"]] = row["cnt"]
            return stats

    def update_fragment_section(self, fragment_id: int, section: str) -> bool:
        """Update the section assignment for a single fragment. Returns True if modified."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE fragments SET section=? WHERE id=?",
                (section, fragment_id),
            )
            return cur.rowcount > 0

    def delete_fragments(self, source_id: str) -> int:
        """Delete all fragments for a source. Returns number of deleted rows."""
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM fragments WHERE source_id=?", (source_id,))
            return cur.rowcount

    def get_intent_coverage(self) -> dict[str, dict[str, int]]:
        """Get fragment counts by section x citation_intent.

        Returns {section: {background: N, method: N, result_comparison: N, total: N}}.
        Only includes sections with at least one fragment with non-NULL intent.
        """
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT section, citation_intent, COUNT(*) as cnt
                   FROM fragments
                   WHERE section IS NOT NULL AND citation_intent IS NOT NULL
                   GROUP BY section, citation_intent
                   ORDER BY section"""
            )
            result: dict[str, dict[str, int]] = {}
            for row in cur.fetchall():
                sec = row["section"]
                if sec not in result:
                    result[sec] = {
                        "background": 0,
                        "method": 0,
                        "result_comparison": 0,
                        "total": 0,
                    }
                intent = row["citation_intent"]
                if intent in result[sec]:
                    result[sec][intent] = row["cnt"]
                    result[sec]["total"] += row["cnt"]
            return result

    def get_embedded_fragment_metadata(
        self, model: Optional[str] = None,
    ) -> list[dict]:
        """Get metadata for fragments that have embeddings.

        Returns list of {id, source_id, section, fragment_text (truncated)}.
        """
        with self._conn() as conn:
            if model:
                cur = conn.execute(
                    """SELECT f.id, f.source_id, f.section, f.chapter,
                              substr(f.fragment_text, 1, 80) as text_preview
                       FROM fragments f
                       WHERE f.embedding IS NOT NULL AND f.embedding_model=?""",
                    (model,),
                )
            else:
                cur = conn.execute(
                    """SELECT f.id, f.source_id, f.section, f.chapter,
                              substr(f.fragment_text, 1, 80) as text_preview
                       FROM fragments f
                       WHERE f.embedding IS NOT NULL""",
                )
            return [dict(row) for row in cur.fetchall()]

    def save_fragment_embedding(
        self, fragment_id: int, embedding: list[float], model: str
    ):
        """Store fragment embedding vector as BLOB with model name."""
        blob = struct.pack(f"{len(embedding)}f", *embedding)
        with self._conn() as conn:
            conn.execute(
                "UPDATE fragments SET embedding=?, embedding_model=? WHERE id=?",
                (blob, model, fragment_id),
            )

    def get_fragment_embeddings(
        self, model: Optional[str] = None
    ) -> dict[int, list[float]]:
        """Get all fragment embeddings, optionally filtered by model.
        Returns {fragment_id: vector}.
        """
        with self._conn() as conn:
            if model:
                cur = conn.execute(
                    "SELECT id, embedding FROM fragments "
                    "WHERE embedding IS NOT NULL AND embedding_model=?",
                    (model,),
                )
            else:
                cur = conn.execute(
                    "SELECT id, embedding FROM fragments WHERE embedding IS NOT NULL"
                )
            result = {}
            for row in cur.fetchall():
                blob = row["embedding"]
                n = len(blob) // 4
                result[row["id"]] = list(struct.unpack(f"{n}f", blob))
            return result

    def get_fragment_embedding_stats(self) -> dict:
        """Get fragment embedding coverage stats."""
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM fragments"
            ).fetchone()["cnt"]
            embedded = conn.execute(
                "SELECT COUNT(*) as cnt FROM fragments WHERE embedding IS NOT NULL"
            ).fetchone()["cnt"]
            models: dict[str, int] = {}
            cur = conn.execute(
                "SELECT embedding_model, COUNT(*) as cnt FROM fragments "
                "WHERE embedding IS NOT NULL GROUP BY embedding_model"
            )
            for row in cur.fetchall():
                models[row["embedding_model"] or "unknown"] = row["cnt"]
            return {"total": total, "embedded": embedded, "models": models}

    def get_unembedded_fragments(self, limit: int = 100000) -> list[dict]:
        """Get fragments without embeddings. Returns id, source_id, fragment_text."""
        with self._conn() as conn:
            cur = conn.execute(
                """SELECT f.id, f.source_id, f.fragment_text, s.id as citekey
                   FROM fragments f
                   JOIN sources s ON f.source_id = s.id
                   WHERE f.embedding IS NULL
                   LIMIT ?""",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    # ── Reassign skips ─────────────────────────────────────────────────

    def save_reassign_skip(
        self, source_id: str, from_section: str, to_section: str,
    ) -> None:
        """Record that the user skipped a reassign suggestion."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO reassign_skips "
                "(source_id, from_section, to_section) VALUES (?, ?, ?)",
                (source_id, from_section, to_section),
            )

    def save_reassign_skips_batch(
        self, skips: list[tuple[str, str, str]],
    ) -> int:
        """Batch-save skip decisions. Each tuple: (source_id, from, to)."""
        with self._conn() as conn:
            for source_id, from_sec, to_sec in skips:
                conn.execute(
                    "INSERT OR REPLACE INTO reassign_skips "
                    "(source_id, from_section, to_section) VALUES (?, ?, ?)",
                    (source_id, from_sec, to_sec),
                )
            return len(skips)

    def get_reassign_skips(self) -> set[tuple[str, str, str]]:
        """Return all skip decisions as {(source_id, from_section, to_section)}."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT source_id, from_section, to_section FROM reassign_skips"
            )
            return {
                (row["source_id"], row["from_section"], row["to_section"])
                for row in cur.fetchall()
            }

    def clear_reassign_skips(self) -> int:
        """Remove all skip decisions. Returns count removed."""
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM reassign_skips")
            return cur.rowcount

    def retrieve_similar_fragments(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        model: Optional[str] = None,
    ) -> list[dict]:
        """Retrieve top-K fragments by cosine similarity to query vector.
        Returns fragment dicts enriched with 'similarity' and 'citekey' fields.
        """
        from ..embeddings import cosine_similarity

        all_emb = self.get_fragment_embeddings(model=model)
        if not all_emb:
            return []

        scored: list[tuple[int, float]] = []
        for frag_id, vec in all_emb.items():
            sim = cosine_similarity(query_embedding, vec)
            scored.append((frag_id, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        top_ids = scored[:top_k]

        if not top_ids:
            return []

        with self._conn() as conn:
            placeholders = ",".join("?" * len(top_ids))
            id_list = [t[0] for t in top_ids]
            cur = conn.execute(
                f"""SELECT f.id, f.source_id, f.fragment_text, f.fragment_type,
                           f.chapter, f.section, f.relevance_score, f.usage_hint,
                           f.page_number, f.citation_intent, s.id as citekey
                    FROM fragments f
                    JOIN sources s ON f.source_id = s.id
                    WHERE f.id IN ({placeholders})""",
                id_list,
            )
            frag_map = {row["id"]: dict(row) for row in cur.fetchall()}

        results = []
        for frag_id, sim in top_ids:
            if frag_id in frag_map:
                frag = frag_map[frag_id]
                frag["similarity"] = round(sim, 4)
                results.append(frag)
        return results
