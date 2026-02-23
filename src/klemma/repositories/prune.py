"""Prune verdicts repository — library audit recommendations."""

from typing import Optional

from .base import BaseRepository

PRUNE_EXPIRY_DAYS = 14


class PruneRepository(BaseRepository):

    def save_prune_verdicts(self, drop: list[dict], maybe: list[dict]):
        """Replace all prune verdicts with fresh results. Hard-protect valuable sources."""
        with self._conn() as conn:
            conn.execute("DELETE FROM prune_verdicts")
            for item in drop:
                ck = item.get("citekey", "").lstrip("@")
                if not ck or self._is_protected(conn, ck):
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO prune_verdicts (source_id, verdict, reason) VALUES (?, 'drop', ?)",
                    (ck, item.get("reason", "")),
                )
            for item in maybe:
                ck = item.get("citekey", "").lstrip("@")
                if not ck or self._is_protected(conn, ck):
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO prune_verdicts (source_id, verdict, reason) VALUES (?, 'maybe', ?)",
                    (ck, item.get("reason", "")),
                )

    @staticmethod
    def _is_protected(conn, source_id: str) -> bool:
        """Check if source is too valuable to prune."""
        cur = conn.execute(
            "SELECT quality_score, relevance_nr1, relevance_nr2, citation_priority FROM sources WHERE id=?",
            (source_id,),
        )
        row = cur.fetchone()
        if not row:
            return True  # unknown source = don't prune
        return (
            (row["quality_score"] or 0) >= 4
            or (row["relevance_nr1"] or 0) >= 4
            or (row["relevance_nr2"] or 0) >= 4
            or row["citation_priority"] == "high"
        )

    def get_prune_drop_ids(self, max_age_days: int = PRUNE_EXPIRY_DAYS) -> set[str]:
        """Return citekeys with verdict='drop' within expiry window."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT source_id FROM prune_verdicts WHERE verdict='drop' AND updated_at > datetime('now', ?)",
                (f"-{max_age_days} days",),
            )
            return {row["source_id"] for row in cur.fetchall()}

    def get_prune_summary(self) -> dict:
        """Return prune verdict counts for status display."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT verdict, COUNT(*) as cnt FROM prune_verdicts "
                "WHERE updated_at > datetime('now', ?) GROUP BY verdict",
                (f"-{PRUNE_EXPIRY_DAYS} days",),
            )
            result = {"drop": 0, "maybe": 0}
            for row in cur.fetchall():
                result[row["verdict"]] = row["cnt"]
            result["total"] = result["drop"] + result["maybe"]
            return result

    def get_prune_verdicts(self, chapter: Optional[int] = None, verdict: Optional[str] = None) -> list[dict]:
        """Get prune verdicts with source metadata, optionally filtered by chapter/verdict."""
        with self._conn() as conn:
            conditions = [f"pv.updated_at > datetime('now', '-{PRUNE_EXPIRY_DAYS} days')"]
            params: list = []

            if verdict:
                conditions.append("pv.verdict = ?")
                params.append(verdict)

            if chapter is not None:
                ch = str(chapter)
                conditions.append(
                    "EXISTS (SELECT 1 FROM source_sections ss2 "
                    "WHERE ss2.source_id = pv.source_id AND (ss2.section = ? OR ss2.section LIKE ?))"
                )
                params.extend([ch, f"{ch}.%"])

            where = " AND ".join(conditions)
            cur = conn.execute(
                f"""SELECT pv.source_id, pv.verdict, pv.reason,
                           s.quality_score, s.fragment_count,
                           GROUP_CONCAT(DISTINCT ss.section) as sections
                    FROM prune_verdicts pv
                    LEFT JOIN sources s ON s.id = pv.source_id
                    LEFT JOIN source_sections ss ON ss.source_id = pv.source_id
                    WHERE {where}
                    GROUP BY pv.source_id
                    ORDER BY pv.verdict, s.quality_score ASC NULLS LAST""",
                params,
            )
            return [dict(row) for row in cur.fetchall()]

    def clear_prune_verdict(self, source_id: str):
        """Remove prune verdict for a source (e.g. when user edits its vault note)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM prune_verdicts WHERE source_id=?", (source_id,))
