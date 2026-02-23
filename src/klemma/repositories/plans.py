"""Daily plans and reading queue repository."""

from datetime import date, timedelta
from typing import Optional

from .base import BaseRepository

# Subquery for filtering out sources marked for pruning
PRUNE_DROP_SUBQUERY = (
    "SELECT source_id FROM prune_verdicts "
    "WHERE verdict='drop' AND updated_at > datetime('now', '-14 days')"
)


class PlansRepository(BaseRepository):

    def save_plan(
        self,
        dissertation_task: str,
        assistant_task: str,
        reading_target: str = "",
        reading_snippet: str = "",
        plan_json: str = "",
        progress_summary: str = "",
    ):
        today = date.today().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO daily_plans
                   (date, dissertation_task, assistant_task, reading_target,
                    reading_snippet, plan_json, progress_summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                   dissertation_task=?, assistant_task=?, reading_target=?,
                   reading_snippet=?, plan_json=?, progress_summary=?""",
                (
                    today, dissertation_task, assistant_task, reading_target,
                    reading_snippet, plan_json, progress_summary,
                    dissertation_task, assistant_task, reading_target,
                    reading_snippet, plan_json, progress_summary,
                ),
            )

    def get_writing_streak(self) -> dict:
        """Calculate writing streak and days without progress from daily_plans."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT date FROM daily_plans ORDER BY date DESC LIMIT 30"
            )
            plan_dates = {row["date"] for row in cur.fetchall()}

        if not plan_dates:
            return {"days_without_progress": 0, "streak": 0, "last_progress_date": None}

        today = date.today()
        days_without = 0
        streak = 0
        last_progress = None

        for i in range(1, 31):
            check = (today - timedelta(days=i)).isoformat()
            if check in plan_dates:
                last_progress = last_progress or check
                streak += 1
            else:
                if last_progress is None:
                    days_without += 1
                else:
                    break

        return {
            "days_without_progress": days_without,
            "streak": streak,
            "last_progress_date": last_progress,
        }

    def get_plan(self, plan_date: Optional[str] = None) -> Optional[dict]:
        d = plan_date or date.today().isoformat()
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM daily_plans WHERE date=?", (d,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_yesterday_plan(self) -> Optional[dict]:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        return self.get_plan(yesterday)

    # ── Reading Queue ────────────────────────────────────────────────────

    def add_to_reading_queue(self, source_id: str, priority: int = 50, total_length: int = 0):
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO reading_queue (source_id, priority, total_length)
                   VALUES (?, ?, ?)""",
                (source_id, priority, total_length),
            )

    def get_next_reading(self) -> Optional[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                f"""SELECT rq.*, s.id as citekey
                   FROM reading_queue rq
                   JOIN sources s ON rq.source_id = s.id
                   WHERE rq.status = 'queued'
                     AND s.id NOT IN ({PRUNE_DROP_SUBQUERY})
                   ORDER BY rq.priority DESC
                   LIMIT 1"""
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def update_reading_position(self, source_id: str, position: int):
        with self._conn() as conn:
            conn.execute(
                """UPDATE reading_queue SET current_position=?, status='reading',
                   started_at=COALESCE(started_at, datetime('now'))
                   WHERE source_id=?""",
                (position, source_id),
            )

    def complete_reading(self, source_id: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE reading_queue SET status='completed', completed_at=datetime('now') WHERE source_id=?",
                (source_id,),
            )
