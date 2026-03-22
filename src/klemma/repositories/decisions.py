"""Guided Serendipity decisions — branching points and researcher choices."""

import json
from typing import Optional

from .base import BaseRepository


class DecisionsRepository(BaseRepository):
    """CRUD for research decisions (Guided Serendipity branching points)."""

    def save_decision(
        self,
        *,
        trigger_type: str,
        trigger_source: Optional[str] = None,
        context: dict,
        options: list[dict],
        sections: Optional[list[str]] = None,
        chosen_option: Optional[str] = None,
        rationale: Optional[str] = None,
        influenced_by: Optional[list[int]] = None,
    ) -> int:
        """Save a new decision (branching point).

        Returns the decision ID.
        """
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO decisions
                   (trigger_type, trigger_source, context_json, options_json,
                    chosen_option, rationale, sections, influenced_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trigger_type,
                    trigger_source,
                    json.dumps(context, ensure_ascii=False),
                    json.dumps(options, ensure_ascii=False),
                    chosen_option,
                    rationale,
                    json.dumps(sections) if sections else None,
                    json.dumps(influenced_by) if influenced_by else None,
                ),
            )
            return cur.lastrowid

    def decide(
        self,
        decision_id: int,
        chosen_option: str,
        rationale: Optional[str] = None,
    ) -> bool:
        """Record the researcher's choice for a pending decision.

        Returns True if the decision was updated.
        """
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE decisions
                   SET chosen_option = ?, rationale = ?, decided_at = datetime('now')
                   WHERE id = ? AND chosen_option IS NULL""",
                (chosen_option, rationale, decision_id),
            )
            return cur.rowcount > 0

    def skip_decision(self, decision_id: int) -> bool:
        """Mark a decision as skipped."""
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE decisions
                   SET chosen_option = '__skipped__', decided_at = datetime('now')
                   WHERE id = ? AND chosen_option IS NULL""",
                (decision_id,),
            )
            return cur.rowcount > 0

    def get_decision(self, decision_id: int) -> Optional[dict]:
        """Get a single decision by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    def get_pending_decisions(
        self, trigger_type: Optional[str] = None
    ) -> list[dict]:
        """Get all decisions without a choice yet."""
        sql = "SELECT * FROM decisions WHERE chosen_option IS NULL"
        params: list = []
        if trigger_type:
            sql += " AND trigger_type = ?"
            params.append(trigger_type)
        sql += " ORDER BY created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_decisions(
        self,
        *,
        section: Optional[str] = None,
        trigger_type: Optional[str] = None,
        limit: int = 50,
        include_skipped: bool = False,
    ) -> list[dict]:
        """Get decisions with optional filters."""
        sql = "SELECT * FROM decisions WHERE 1=1"
        params: list = []

        if not include_skipped:
            sql += " AND (chosen_option IS NULL OR chosen_option != '__skipped__')"

        if trigger_type:
            sql += " AND trigger_type = ?"
            params.append(trigger_type)

        if section:
            sql += " AND sections LIKE ?"
            params.append(f'%"{section}"%')

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_trail(self) -> list[dict]:
        """Get the full decision trail (only decided, ordered chronologically).

        Returns decisions with their influenced_by links for graph construction.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM decisions
                   WHERE chosen_option IS NOT NULL
                     AND chosen_option != '__skipped__'
                   ORDER BY created_at ASC"""
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_decisions_for_context(
        self, sections: Optional[list[str]] = None
    ) -> list[dict]:
        """Get decided decisions suitable for prompt injection.

        Returns compact summaries for downstream use in research/draft prompts.
        """
        sql = """SELECT id, trigger_type, trigger_source, chosen_option,
                        rationale, sections, created_at
                 FROM decisions
                 WHERE chosen_option IS NOT NULL
                   AND chosen_option != '__skipped__'"""
        params: list = []

        if sections:
            conditions = " OR ".join(
                "sections LIKE ?" for _ in sections
            )
            sql += f" AND ({conditions})"
            params.extend(f'%"{s}"%' for s in sections)

        sql += " ORDER BY created_at ASC"

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def count_decisions(self) -> dict:
        """Return counts by status."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT
                     COUNT(*) as total,
                     SUM(CASE WHEN chosen_option IS NULL THEN 1 ELSE 0 END) as pending,
                     SUM(CASE WHEN chosen_option IS NOT NULL
                          AND chosen_option != '__skipped__' THEN 1 ELSE 0 END) as decided,
                     SUM(CASE WHEN chosen_option = '__skipped__' THEN 1 ELSE 0 END) as skipped
                   FROM decisions"""
            ).fetchone()
            return dict(row)

    @staticmethod
    def _row_to_dict(row) -> dict:
        """Convert a sqlite3.Row to a dict with parsed JSON fields."""
        d = dict(row)
        for key in ("context_json", "options_json", "sections", "influenced_by"):
            if d.get(key) and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d
