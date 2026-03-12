"""Tests for benchmark run history — save, get, compare, migration."""

import pytest

from klemma.evaluation.runners import build_results_summary
from klemma.repositories.benchmarks import compute_dataset_hash
from klemma.state import StateManager


@pytest.fixture
def state(tmp_path):
    return StateManager(tmp_path / "test.db")


class TestBenchmarkRepository:
    def test_save_and_get_roundtrip(self, state):
        results = {"reconstruction": {"baseline": {"source_coverage": 1.0}}}
        summary = {"reconstruction.baseline.source_coverage": 1.0}
        run_id = state.save_benchmark_run(
            dataset_path="/tmp/test.json",
            dataset_hash="abc123",
            metrics_filter="reconstruct",
            ai_backend="claude",
            ai_model="opus",
            results=results,
            results_summary=summary,
            paper_citekey="kinney2025",
            duration_seconds=12.5,
            git_commit="31beb1d",
            klemma_version="0.4.1",
            config_snapshot={"ai": {"model": "opus"}},
        )
        assert len(run_id) == 12

        run = state.get_benchmark_run(run_id)
        assert run is not None
        assert run["paper_citekey"] == "kinney2025"
        assert run["results"]["reconstruction"]["baseline"]["source_coverage"] == 1.0
        assert run["results_summary"]["reconstruction.baseline.source_coverage"] == 1.0
        assert run["duration_seconds"] == 12.5
        assert run["git_commit"] == "31beb1d"
        assert run["config_snapshot"]["ai"]["model"] == "opus"

    def test_get_runs_ordered_newest_first(self, state):
        for i in range(5):
            state.save_benchmark_run(
                results={"i": i},
                results_summary={"val": float(i)},
            )
        runs = state.get_benchmark_runs(limit=3)
        assert len(runs) == 3
        # Newest first: timestamps should be non-increasing
        assert runs[0]["timestamp"] >= runs[1]["timestamp"]

    def test_get_runs_filtered_by_paper(self, state):
        state.save_benchmark_run(
            results={}, results_summary={}, paper_citekey="paper_a",
        )
        state.save_benchmark_run(
            results={}, results_summary={}, paper_citekey="paper_b",
        )
        runs = state.get_benchmark_runs(paper_citekey="paper_a")
        assert len(runs) == 1
        assert runs[0]["paper_citekey"] == "paper_a"

    def test_get_latest_run(self, state):
        state.save_benchmark_run(
            results={"v": 1}, results_summary={}, paper_citekey="p",
        )
        state.save_benchmark_run(
            results={"v": 2}, results_summary={}, paper_citekey="p",
        )
        latest = state.get_latest_benchmark_run(paper_citekey="p")
        assert latest["results"]["v"] == 2

    def test_get_latest_run_none(self, state):
        assert state.get_latest_benchmark_run() is None

    def test_compare_runs(self, state):
        id_a = state.save_benchmark_run(
            results={}, results_summary={"f1": 0.5, "recall": 0.6},
        )
        id_b = state.save_benchmark_run(
            results={}, results_summary={"f1": 0.7, "recall": 0.6},
        )
        cmp = state.compare_benchmark_runs(id_a, id_b)
        assert cmp["deltas"]["f1"]["a"] == 0.5
        assert cmp["deltas"]["f1"]["b"] == 0.7
        assert cmp["deltas"]["f1"]["delta"] == pytest.approx(0.2)
        assert cmp["deltas"]["recall"]["delta"] == pytest.approx(0.0)

    def test_compare_missing_run(self, state):
        id_a = state.save_benchmark_run(results={}, results_summary={})
        cmp = state.compare_benchmark_runs(id_a, "nonexistent")
        assert "error" in cmp

    def test_get_benchmarked_citekeys(self, state):
        state.save_benchmark_run(
            results={}, results_summary={}, paper_citekey="alpha",
        )
        state.save_benchmark_run(
            results={}, results_summary={}, paper_citekey="beta",
        )
        state.save_benchmark_run(
            results={}, results_summary={}, paper_citekey="alpha",
        )
        keys = state.get_benchmarked_citekeys()
        assert keys == {"alpha", "beta"}

    def test_get_nonexistent_run(self, state):
        assert state.get_benchmark_run("nope") is None


class TestMigration:
    def test_migration_idempotent(self, tmp_path):
        """Opening DB twice should not fail (CREATE TABLE IF NOT EXISTS)."""
        db_path = tmp_path / "migrate.db"
        s1 = StateManager(db_path)
        s1.save_benchmark_run(results={"x": 1}, results_summary={})
        s2 = StateManager(db_path)
        runs = s2.get_benchmark_runs()
        assert len(runs) == 1

    def test_schema_version_is_4(self, state):
        import sqlite3
        conn = sqlite3.connect(state.db_path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == 12


class TestBuildResultsSummary:
    def test_intent_metrics(self):
        results = {
            "intent": {"metrics": {"macro_f1": 0.85, "accuracy": 0.90}},
        }
        summary = build_results_summary(results)
        assert summary["intent.macro_f1"] == 0.85
        assert summary["intent.accuracy"] == 0.90

    def test_reconstruction_metrics(self):
        results = {
            "reconstruction": {
                "baseline": {"source_coverage": 1.0, "intent_coverage": 0.625},
                "reconstruction": {
                    "f1": 0.624, "macro_precision": 0.7,
                    "macro_recall": 0.6, "intent_accuracy": 0.5,
                    "ndcg_avg": 0.8,
                },
            },
        }
        summary = build_results_summary(results)
        assert summary["reconstruction.baseline.source_coverage"] == 1.0
        assert summary["reconstruction.f1"] == 0.624

    def test_empty_results(self):
        assert build_results_summary({}) == {}

    def test_gaps_and_embeddings(self):
        results = {
            "gaps": {"metrics": {"precision_at_5": 0.4, "ndcg_at_10": 0.6}},
            "embeddings": {"metrics": {"avg_recall_at_10": 0.7}},
        }
        summary = build_results_summary(results)
        assert summary["gaps.precision_at_5"] == 0.4
        assert summary["embeddings.avg_recall_at_10"] == 0.7


class TestComputeDatasetHash:
    def test_hash_of_file(self, tmp_path):
        p = tmp_path / "test.json"
        p.write_text('{"version": "1.0"}')
        h = compute_dataset_hash(str(p))
        assert len(h) == 16
        # Deterministic
        assert compute_dataset_hash(str(p)) == h

    def test_hash_of_missing_file(self):
        assert compute_dataset_hash("/nonexistent/path") == ""
