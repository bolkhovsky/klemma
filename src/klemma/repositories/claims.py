"""Manuscript claims ledger repository — durable state of the citation audit."""

from typing import Optional

from .base import BaseRepository


class ClaimsRepository(BaseRepository):
    """Claims ledger: one row per (manuscript, claim_hash, anchor_key).

    Identity is content-based (see ``citation_checker.compute_claim_hash``):
    editing a sentence changes its hash, so the old row goes stale and the
    new one starts unchecked — staleness by design, no diffing needed.
    The ledger survives between sessions and backs both the ``--incremental``
    replay in check-citations and the ``klemma claims status --gate``
    submission gate.
    """

    def record_check(
        self,
        manuscript_path: str,
        entries: list[dict],
        judge_model: Optional[str] = None,
    ) -> int:
        """UPSERT audit entries for one manuscript; returns rows written.

        Conflict target is UNIQUE(manuscript_path, claim_hash, anchor_key):
        a re-run refreshes char range, verdict, reason, evidence_* and
        verified_at, and revives previously stale rows (stale=0).
        judge_model lands only on rows whose verdict came from AI —
        provenance next to AI output, NULL for deterministic verdicts.
        A replayed AI verdict (--incremental run with no fresh judge calls)
        arrives with judge_model=None — COALESCE keeps the original model
        name instead of erasing the provenance.
        """
        written = 0
        with self._conn() as conn:
            for e in entries:
                verdict = e.get("verdict")
                ai_used = 1 if e.get("ai_used") else 0
                conn.execute(
                    """INSERT INTO claims
                       (manuscript_path, claim_hash, anchor_key, sentence,
                        citekey, ref_number, location, char_start, char_end,
                        anchor_kind, anchor_raw, verdict, reason, ai_used,
                        judge_model, evidence_start, evidence_end,
                        evidence_locator, verified_at, stale)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               CASE WHEN ? IS NOT NULL THEN datetime('now') END, 0)
                       ON CONFLICT(manuscript_path, claim_hash, anchor_key)
                       DO UPDATE SET
                           sentence=excluded.sentence,
                           citekey=excluded.citekey,
                           ref_number=excluded.ref_number,
                           location=excluded.location,
                           char_start=excluded.char_start,
                           char_end=excluded.char_end,
                           anchor_kind=excluded.anchor_kind,
                           anchor_raw=excluded.anchor_raw,
                           verdict=excluded.verdict,
                           reason=excluded.reason,
                           ai_used=excluded.ai_used,
                           judge_model=CASE WHEN excluded.ai_used=1
                               THEN COALESCE(excluded.judge_model, claims.judge_model)
                               ELSE NULL END,
                           evidence_start=excluded.evidence_start,
                           evidence_end=excluded.evidence_end,
                           evidence_locator=excluded.evidence_locator,
                           verified_at=excluded.verified_at,
                           stale=0""",
                    (
                        manuscript_path,
                        e["claim_hash"],
                        e.get("anchor_key", ""),
                        e.get("sentence", ""),
                        e.get("citekey", ""),
                        e.get("ref_number"),
                        e.get("location"),
                        e.get("char_start", 0),
                        e.get("char_end", 0),
                        e.get("anchor_kind"),
                        e.get("anchor_raw"),
                        verdict,
                        e.get("reason"),
                        ai_used,
                        judge_model if ai_used else None,
                        e.get("evidence_start"),
                        e.get("evidence_end"),
                        e.get("evidence_locator"),
                        verdict,
                    ),
                )
                written += 1
        return written

    def mark_stale(self, manuscript_path: str, live_hashes: set[str]) -> int:
        """Mark rows whose claim_hash vanished from the fresh parse.

        Returns the number of rows newly marked stale.
        """
        with self._conn() as conn:
            if live_hashes:
                placeholders = ",".join("?" * len(live_hashes))
                cur = conn.execute(
                    f"""UPDATE claims SET stale=1
                        WHERE manuscript_path=? AND stale=0
                          AND claim_hash NOT IN ({placeholders})""",
                    (manuscript_path, *live_hashes),
                )
            else:
                cur = conn.execute(
                    "UPDATE claims SET stale=1 WHERE manuscript_path=? AND stale=0",
                    (manuscript_path,),
                )
            return cur.rowcount

    def get_claims(
        self, manuscript_path: str, include_stale: bool = True
    ) -> list[dict]:
        """All ledger rows for one manuscript, in manuscript order."""
        query = "SELECT * FROM claims WHERE manuscript_path=?"
        if not include_stale:
            query += " AND stale=0"
        query += " ORDER BY char_start, anchor_key"
        with self._conn() as conn:
            cur = conn.execute(query, (manuscript_path,))
            return [dict(row) for row in cur.fetchall()]

    def get_status_summary(
        self, manuscript_path: Optional[str] = None
    ) -> list[dict]:
        """Per-manuscript counters for the submission gate.

        Stale rows count only under ``stale`` (their old verdict no longer
        applies to the current text); live rows with verdict NULL are
        ``unchecked``.
        """
        where = ""
        params: tuple = ()
        if manuscript_path:
            where = "WHERE manuscript_path=?"
            params = (manuscript_path,)
        with self._conn() as conn:
            cur = conn.execute(
                f"""SELECT manuscript_path,
                           COUNT(*) AS total,
                           SUM(stale) AS stale,
                           SUM(CASE WHEN stale=0 AND verdict IS NULL
                               THEN 1 ELSE 0 END) AS unchecked,
                           SUM(CASE WHEN stale=0 AND verdict='ok'
                               THEN 1 ELSE 0 END) AS ok,
                           SUM(CASE WHEN stale=0 AND verdict='soft_warn'
                               THEN 1 ELSE 0 END) AS soft_warn,
                           SUM(CASE WHEN stale=0 AND verdict='hard_warn'
                               THEN 1 ELSE 0 END) AS hard_warn,
                           SUM(CASE WHEN stale=0 AND verdict='unverifiable'
                               THEN 1 ELSE 0 END) AS unverifiable,
                           SUM(CASE WHEN stale=0 AND verdict='error'
                               THEN 1 ELSE 0 END) AS error,
                           MAX(verified_at) AS last_verified
                    FROM claims {where}
                    GROUP BY manuscript_path
                    ORDER BY manuscript_path""",
                params,
            )
            return [dict(row) for row in cur.fetchall()]
