"""Benchmark run history repository."""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from .base import BaseRepository


class BenchmarkRepository(BaseRepository):

    def save_run(
        self,
        *,
        dataset_path: str = "",
        dataset_hash: str = "",
        metrics_filter: str = "all",
        ai_backend: str = "",
        ai_model: str = "",
        results: dict,
        results_summary: dict,
        paper_citekey: str = "",
        duration_seconds: float = 0.0,
        git_commit: str = "",
        klemma_version: str = "",
        config_snapshot: dict | None = None,
    ) -> str:
        """Persist a benchmark run. Returns run_id."""
        run_id = uuid.uuid4().hex[:12]
        ts = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO benchmark_runs
                   (run_id, timestamp, dataset_path, dataset_hash,
                    metrics_filter, ai_backend, ai_model,
                    results, results_summary, paper_citekey,
                    duration_seconds, git_commit, klemma_version, config_snapshot)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, ts, dataset_path, dataset_hash,
                    metrics_filter, ai_backend, ai_model,
                    json.dumps(results), json.dumps(results_summary),
                    paper_citekey, duration_seconds,
                    git_commit, klemma_version,
                    json.dumps(config_snapshot) if config_snapshot else "{}",
                ),
            )
        return run_id

    def get_runs(self, limit: int = 20, paper_citekey: str = "") -> list[dict]:
        """Return recent benchmark runs, newest first."""
        with self._conn() as conn:
            if paper_citekey:
                cur = conn.execute(
                    "SELECT * FROM benchmark_runs WHERE paper_citekey=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (paper_citekey, limit),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM benchmark_runs ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
            return [_row_to_dict(row) for row in cur.fetchall()]

    def get_run(self, run_id: str) -> Optional[dict]:
        """Return a single run by ID."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM benchmark_runs WHERE run_id=?", (run_id,)
            )
            row = cur.fetchone()
            return _row_to_dict(row) if row else None

    def get_latest_run(self, paper_citekey: str = "") -> Optional[dict]:
        """Return the most recent run, optionally filtered by paper."""
        with self._conn() as conn:
            if paper_citekey:
                cur = conn.execute(
                    "SELECT * FROM benchmark_runs WHERE paper_citekey=? "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (paper_citekey,),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM benchmark_runs ORDER BY timestamp DESC LIMIT 1"
                )
            row = cur.fetchone()
            return _row_to_dict(row) if row else None

    def compare_runs(self, id_a: str, id_b: str) -> dict:
        """Compute delta for shared metric keys between two runs."""
        a = self.get_run(id_a)
        b = self.get_run(id_b)
        if not a or not b:
            return {"error": "one or both runs not found"}

        sa = a.get("results_summary", {})
        sb = b.get("results_summary", {})
        all_keys = sorted(set(sa) | set(sb))
        deltas: dict[str, dict] = {}
        for key in all_keys:
            va = sa.get(key)
            vb = sb.get(key)
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                deltas[key] = {"a": va, "b": vb, "delta": round(vb - va, 6)}
            else:
                deltas[key] = {"a": va, "b": vb, "delta": None}

        return {
            "run_a": id_a,
            "run_b": id_b,
            "timestamp_a": a.get("timestamp", ""),
            "timestamp_b": b.get("timestamp", ""),
            "deltas": deltas,
        }

    def get_benchmarked_citekeys(self) -> set[str]:
        """Return set of paper_citekeys that have been benchmarked."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT DISTINCT paper_citekey FROM benchmark_runs "
                "WHERE paper_citekey != ''"
            )
            return {row[0] for row in cur.fetchall()}


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to dict, parsing JSON fields."""
    d = dict(row)
    for field in ("results", "results_summary", "config_snapshot"):
        if field in d and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def compute_dataset_hash(path: str) -> str:
    """SHA256 of dataset file contents."""
    try:
        data = open(path, "rb").read()
        return hashlib.sha256(data).hexdigest()[:16]
    except OSError:
        return ""
