"""Tests for evaluation framework — metrics, dataset, runners."""

import json

import pytest

from klemma.evaluation.dataset import (
    BenchmarkDataset,
    GapSample,
    IntentSample,
    SimilarityPair,
    export_dataset,
    load_dataset,
)
from klemma.evaluation.metrics import (
    intent_metrics,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from klemma.evaluation.runners import (
    run_all,
    run_embedding_benchmark,
    run_gap_benchmark,
    run_intent_benchmark,
)
from klemma.state import StateManager

# --- Pure metrics tests ---


class TestIntentMetrics:
    """Test intent_metrics (macro-F1, accuracy, per-class, confusion)."""

    def test_perfect_match(self):
        preds = ["background", "method", "result_comparison"]
        truth = ["background", "method", "result_comparison"]
        result = intent_metrics(preds, truth)
        assert result["macro_f1"] == 1.0
        assert result["accuracy"] == 1.0
        assert result["total"] == 3
        for cls in ("background", "method", "result_comparison"):
            assert result["per_class"][cls]["f1"] == 1.0

    def test_no_match(self):
        preds = ["background", "background", "background"]
        truth = ["method", "result_comparison", "method"]
        result = intent_metrics(preds, truth)
        assert result["accuracy"] == 0.0
        assert result["macro_f1"] == 0.0

    def test_partial_match(self):
        preds = ["background", "method", "background"]
        truth = ["background", "method", "method"]
        result = intent_metrics(preds, truth)
        assert result["accuracy"] == pytest.approx(2 / 3, abs=0.01)
        assert result["macro_f1"] > 0
        assert result["per_class"]["background"]["precision"] < 1.0

    def test_empty_lists(self):
        result = intent_metrics([], [])
        assert result["total"] == 0
        assert result["macro_f1"] == 0.0

    def test_confusion_matrix_structure(self):
        preds = ["background", "method", "background"]
        truth = ["background", "background", "method"]
        result = intent_metrics(preds, truth)
        confusion = result["confusion"]
        assert confusion["background"]["background"] == 1
        assert confusion["background"]["method"] == 1
        assert confusion["method"]["background"] == 1

    def test_macro_f1_handles_imbalanced(self):
        """Macro-F1 should not be inflated by majority class."""
        preds = ["background"] * 8 + ["method", "result_comparison"]
        truth = ["background"] * 6 + ["method"] * 2 + ["result_comparison"] * 2
        result = intent_metrics(preds, truth)
        # Accuracy is high (6/10 background correct) but macro-F1 is lower
        # because method and result_comparison have low recall
        assert result["accuracy"] > result["macro_f1"]


class TestPrecisionAtK:
    def test_all_relevant(self):
        ranked = ["a", "b", "c", "d"]
        relevant = {"a", "b", "c", "d"}
        assert precision_at_k(ranked, relevant, 4) == 1.0

    def test_none_relevant(self):
        ranked = ["a", "b", "c"]
        relevant = {"x", "y"}
        assert precision_at_k(ranked, relevant, 3) == 0.0

    def test_half_relevant(self):
        ranked = ["a", "b", "c", "d"]
        relevant = {"a", "c"}
        assert precision_at_k(ranked, relevant, 4) == 0.5

    def test_k_larger_than_list(self):
        ranked = ["a", "b"]
        relevant = {"a", "b"}
        assert precision_at_k(ranked, relevant, 10) == 1.0

    def test_k_zero(self):
        assert precision_at_k(["a"], {"a"}, 0) == 0.0

    def test_empty_relevant(self):
        assert precision_at_k(["a", "b"], set(), 2) == 0.0


class TestRecallAtK:
    def test_all_found(self):
        ranked = ["a", "b", "c"]
        relevant = {"a", "b"}
        assert recall_at_k(ranked, relevant, 3) == 1.0

    def test_none_found(self):
        ranked = ["x", "y", "z"]
        relevant = {"a", "b"}
        assert recall_at_k(ranked, relevant, 3) == 0.0

    def test_partial_found(self):
        ranked = ["a", "x", "b", "y"]
        relevant = {"a", "b", "c"}
        assert recall_at_k(ranked, relevant, 2) == pytest.approx(1 / 3, abs=0.01)


class TestNDCG:
    def test_perfect_ranking(self):
        ranked = ["a", "b", "c"]
        relevance = {"a": 5, "b": 3, "c": 1}
        result = ndcg_at_k(ranked, relevance, 3)
        assert result == 1.0

    def test_reversed_ranking(self):
        ranked = ["c", "b", "a"]
        relevance = {"a": 5, "b": 3, "c": 1}
        result = ndcg_at_k(ranked, relevance, 3)
        assert result < 1.0
        assert result > 0.0

    def test_empty(self):
        assert ndcg_at_k([], {}, 5) == 0.0

    def test_k_zero(self):
        assert ndcg_at_k(["a"], {"a": 5}, 0) == 0.0

    def test_single_item(self):
        assert ndcg_at_k(["a"], {"a": 5}, 1) == 1.0

    def test_unknown_items_get_zero_relevance(self):
        ranked = ["unknown", "a"]
        relevance = {"a": 5}
        result = ndcg_at_k(ranked, relevance, 2)
        assert result < 1.0


# --- Dataset tests ---


class TestDataset:
    def test_load_valid(self, tmp_path):
        data = {
            "version": "1.0",
            "fragments": [
                {"source_id": "s1", "fragment_text": "test", "ground_truth": "method"}
            ],
            "gaps": [
                {"ref_title": "Paper X", "section": "2.1",
                 "ground_truth_relevance": 4}
            ],
            "similar_pairs": [
                {"query_source": "s1", "relevant": ["s2", "s3"]}
            ],
        }
        p = tmp_path / "ds.json"
        p.write_text(json.dumps(data))
        ds = load_dataset(p)
        assert len(ds.fragments) == 1
        assert ds.fragments[0].ground_truth == "method"
        assert len(ds.gaps) == 1
        assert len(ds.similar_pairs) == 1

    def test_load_invalid_intent(self, tmp_path):
        data = {
            "fragments": [
                {"source_id": "s1", "fragment_text": "x",
                 "ground_truth": "INVALID"}
            ],
        }
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(data))
        with pytest.raises(Exception):
            load_dataset(p)

    def test_load_invalid_relevance(self, tmp_path):
        data = {
            "gaps": [
                {"ref_title": "X", "section": "1.1",
                 "ground_truth_relevance": 10}
            ],
        }
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(data))
        with pytest.raises(Exception):
            load_dataset(p)

    def test_roundtrip(self, tmp_path):
        ds = BenchmarkDataset(
            fragments=[
                IntentSample(source_id="s1", fragment_text="text",
                             ground_truth="background")
            ],
            gaps=[
                GapSample(ref_title="T", section="1.1",
                          ground_truth_relevance=3)
            ],
            similar_pairs=[
                SimilarityPair(query_source="s1", relevant=["s2"])
            ],
        )
        p = tmp_path / "rt.json"
        p.write_text(json.dumps(ds.model_dump()))
        loaded = load_dataset(p)
        assert loaded.fragments[0].source_id == "s1"
        assert loaded.gaps[0].ref_title == "T"
        assert loaded.similar_pairs[0].relevant == ["s2"]

    def test_empty_dataset(self):
        ds = BenchmarkDataset()
        assert ds.fragments == []
        assert ds.gaps == []
        assert ds.similar_pairs == []


# --- Runner integration tests (with StateManager) ---


@pytest.fixture
def state(tmp_path):
    return StateManager(tmp_path / "test.db")


class TestRunIntentBenchmark:
    def test_matches_fragments(self, state):
        state.register_sources(["s1"])
        state.save_fragments("s1", [
            {"text": "This is about background info", "type": "key_idea",
             "section": "1.1", "relevance": 3, "citation_intent": "background"},
            {"text": "Method description here", "type": "methodology",
             "section": "2.1", "relevance": 4, "citation_intent": "method"},
        ])
        ds = BenchmarkDataset(fragments=[
            IntentSample(source_id="s1",
                         fragment_text="This is about background info",
                         ground_truth="background"),
            IntentSample(source_id="s1",
                         fragment_text="Method description here",
                         ground_truth="method"),
        ])
        result = run_intent_benchmark(state, ds)
        assert result["matched"] == 2
        assert result["skipped"] == 0
        assert result["metrics"]["macro_f1"] == 1.0

    def test_skips_unmatched(self, state):
        state.register_sources(["s1"])
        ds = BenchmarkDataset(fragments=[
            IntentSample(source_id="s1",
                         fragment_text="nonexistent fragment",
                         ground_truth="method"),
        ])
        result = run_intent_benchmark(state, ds)
        assert result["matched"] == 0
        assert result["skipped"] == 1

    def test_empty_dataset(self, state):
        result = run_intent_benchmark(state, BenchmarkDataset())
        assert result["total"] == 0


class TestRunGapBenchmark:
    def test_evaluates_ranking(self, state):
        state.register_sources(["s1"])
        state.save_reference_gaps("s1", [
            {"ref_authors": "A", "ref_year": 2020, "ref_title": "Paper Alpha",
             "why_relevant": "r", "citation_intent": "method"},
            {"ref_authors": "B", "ref_year": 2021, "ref_title": "Paper Beta",
             "why_relevant": "r", "citation_intent": "background"},
        ])
        ds = BenchmarkDataset(gaps=[
            GapSample(ref_title="Paper Alpha", section="2.1",
                      ground_truth_relevance=5),
            GapSample(ref_title="Paper Beta", section="1.1",
                      ground_truth_relevance=2),
        ])
        result = run_gap_benchmark(state, ds)
        assert result["total"] == 2
        assert "precision_at_5" in result["metrics"]
        assert "ndcg_at_10" in result["metrics"]

    def test_empty_dataset(self, state):
        result = run_gap_benchmark(state, BenchmarkDataset())
        assert result["total"] == 0

    def test_reranked_gaps_skips_db(self, state):
        """When reranked_gaps is provided, DB is not fetched — the given list is used.

        Proof: reranked list contains only an unrelated paper (not in ground truth),
        so precision falls to 0. If DB were consulted instead, it would return
        the relevant paper and precision would be 1.0.
        """
        state.register_sources(["s1"])
        state.save_reference_gaps("s1", [
            {"ref_authors": "A", "ref_year": 2020, "ref_title": "Relevant Paper",
             "why_relevant": "r", "citation_intent": "method"},
        ])
        ds = BenchmarkDataset(gaps=[
            GapSample(ref_title="Relevant Paper", section="2.1",
                      ground_truth_relevance=5),
        ])

        # Without reranking: DB has the relevant paper → P@5 = 1.0
        result_default = run_gap_benchmark(state, ds)
        assert result_default["metrics"]["precision_at_5"] == 1.0

        # With reranked list containing only an unrelated paper — DB is skipped,
        # so the relevant paper is absent → P@5 = 0.0
        reranked = [{"ref_title": "Completely Unrelated Paper", "score": 20.0}]
        result_reranked = run_gap_benchmark(state, ds, reranked_gaps=reranked)
        assert result_reranked["metrics"]["precision_at_5"] == 0.0
        # db_gaps_count reflects the reranked list length, not the DB count
        assert result_reranked["db_gaps_count"] == 1


class TestRunEmbeddingBenchmark:
    def test_computes_recall(self, state):
        state.register_sources(["q", "a", "b", "c"])
        for src in ["q", "a", "b", "c"]:
            state.mark_completed(src, f"/notes/{src}.md")
        # q is close to a (similar vectors), far from b and c
        state.save_embedding("q", [1.0, 0.0, 0.0], "test")
        state.save_embedding("a", [0.9, 0.1, 0.0], "test")
        state.save_embedding("b", [0.0, 1.0, 0.0], "test")
        state.save_embedding("c", [0.0, 0.0, 1.0], "test")

        ds = BenchmarkDataset(similar_pairs=[
            SimilarityPair(query_source="q", relevant=["a"]),
        ])
        result = run_embedding_benchmark(state, ds)
        assert result["evaluated"] == 1
        assert result["metrics"]["avg_recall_at_5"] == 1.0

    def test_no_embeddings(self, state):
        ds = BenchmarkDataset(similar_pairs=[
            SimilarityPair(query_source="q", relevant=["a"]),
        ])
        result = run_embedding_benchmark(state, ds)
        assert result.get("error") == "no embeddings in database"

    def test_empty_dataset(self, state):
        result = run_embedding_benchmark(state, BenchmarkDataset())
        assert result["total_queries"] == 0


class TestRunAll:
    def test_dispatches_selected(self, state):
        ds = BenchmarkDataset(
            fragments=[
                IntentSample(source_id="s1", fragment_text="x",
                             ground_truth="method")
            ],
            gaps=[
                GapSample(ref_title="T", section="1.1",
                          ground_truth_relevance=3)
            ],
        )
        result = run_all(state, ds, "intent")
        assert "intent" in result
        assert "gaps" not in result

    def test_all_filter(self, state):
        state.register_sources(["s1"])
        state.save_fragments("s1", [
            {"text": "test frag", "type": "key_idea", "section": "1.1",
             "relevance": 3, "citation_intent": "background"},
        ])
        ds = BenchmarkDataset(
            fragments=[
                IntentSample(source_id="s1", fragment_text="test frag",
                             ground_truth="background")
            ],
        )
        result = run_all(state, ds, "all")
        assert "intent" in result


class TestExportDataset:
    def test_exports_template(self, state, tmp_path):
        state.register_sources(["s1"])
        state.save_fragments("s1", [
            {"text": "A background fragment", "type": "key_idea",
             "section": "1.1", "relevance": 3, "citation_intent": "background"},
        ])
        state.save_reference_gaps("s1", [
            {"ref_authors": "Smith", "ref_year": 2020, "ref_title": "Test Paper",
             "why_relevant": "important", "citation_intent": "method"},
        ])
        out = tmp_path / "export.json"
        count = export_dataset(state, out)
        assert count > 0
        ds = load_dataset(out)
        assert len(ds.fragments) >= 1
        assert ds.fragments[0].ground_truth == "background"
