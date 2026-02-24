"""Fragment repository — CRUD and intent coverage stats."""

from typing import Optional

from .base import BaseRepository


class FragmentRepository(BaseRepository):

    def save_fragments(self, source_id: str, fragments: list[dict]) -> int:
        """Save extracted fragments and update source fragment count."""
        with self._conn() as conn:
            for f in fragments:
                conn.execute(
                    """INSERT INTO fragments
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
            conn.execute(
                "UPDATE sources SET fragment_count=? WHERE id=?",
                (len(fragments), source_id),
            )
            return len(fragments)

    def get_fragments(
        self,
        source_id: Optional[str] = None,
        chapter: Optional[int] = None,
        section: Optional[str] = None,
        fragment_type: Optional[str] = None,
        limit: int = 50,
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
