"""Benchmark runners — orchestrate DB queries and metric computation.

Each runner implements one evaluation format following SciRepEval design
(Singh et al. 2023): intent = classification, gaps = ranking, embeddings = retrieval.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from klemma.embeddings import cosine_similarity

from .dataset import BenchmarkDataset
from .metrics import intent_metrics, ndcg_at_k, precision_at_k, recall_at_k

if TYPE_CHECKING:
    from klemma.state import StateManager


def run_intent_benchmark(
    state: StateManager, dataset: BenchmarkDataset
) -> dict:
    """Compare DB fragment intents vs ground truth labels.

    Matches fragments by (source_id, text substring).
    Primary metric: macro_f1 (Cohan et al. 2019; Subramanian et al. 2021).
    """
    if not dataset.fragments:
        return {"total": 0, "matched": 0, "skipped": 0, "metrics": {}}

    predictions = []
    ground_truth = []
    skipped = 0

    for sample in dataset.fragments:
        db_frags = state.get_fragments(source_id=sample.source_id, limit=500)
        matched_intent = None
        for frag in db_frags:
            if sample.fragment_text[:80] in frag.get("fragment_text", ""):
                matched_intent = frag.get("citation_intent")
                break

        if matched_intent:
            predictions.append(matched_intent)
            ground_truth.append(sample.ground_truth)
        else:
            skipped += 1

    metrics = intent_metrics(predictions, ground_truth)

    return {
        "total": len(dataset.fragments),
        "matched": len(predictions),
        "skipped": skipped,
        "metrics": metrics,
    }


def run_gap_benchmark(
    state: StateManager,
    dataset: BenchmarkDataset,
    reranked_gaps: list[dict] | None = None,
) -> dict:
    """Evaluate gap scoring precision against ground truth relevance.

    Gets current gap ranking from DB, matches against annotated relevance.
    Metrics: precision@5, precision@10, nDCG@10 (Bhagavatula et al. 2018;
    Cohan et al. 2020).

    Args:
        reranked_gaps: Pre-ranked gap list (e.g. from rerank_gaps_semantic).
            When provided, skips DB fetch and uses this list directly.
    """
    if not dataset.gaps:
        return {"total": 0, "metrics": {}}

    # Build relevance map from ground truth: title -> relevance score
    relevance_map: dict[str, int] = {}
    relevant_titles: set[str] = set()
    for sample in dataset.gaps:
        title_key = sample.ref_title.lower().strip()
        relevance_map[title_key] = sample.ground_truth_relevance
        if sample.ground_truth_relevance >= 3:
            relevant_titles.add(title_key)

    # Use provided reranked list or fall back to DB ranking
    db_gaps = reranked_gaps if reranked_gaps is not None else state.get_reference_gaps(limit=100)
    ranked_titles = [
        (g.get("ref_title", "") or "").lower().strip() for g in db_gaps
    ]

    return {
        "total": len(dataset.gaps),
        "db_gaps_count": len(db_gaps),
        "metrics": {
            "precision_at_5": precision_at_k(ranked_titles, relevant_titles, 5),
            "precision_at_10": precision_at_k(ranked_titles, relevant_titles, 10),
            "ndcg_at_10": ndcg_at_k(ranked_titles, relevance_map, 10),
        },
    }


def run_embedding_benchmark(
    state: StateManager, dataset: BenchmarkDataset
) -> dict:
    """Evaluate embedding retrieval quality.

    For each query_source, computes cosine similarity against all stored
    embeddings and checks if ground truth relevant sources appear in top-K.
    Metrics: recall@5, recall@10, precision@5 (Singh et al. 2023;
    Cohan et al. 2020).
    """
    if not dataset.similar_pairs:
        return {"total_queries": 0, "metrics": {}}

    all_embeddings = state.get_all_embeddings()
    if not all_embeddings:
        return {
            "total_queries": len(dataset.similar_pairs),
            "metrics": {},
            "error": "no embeddings in database",
        }

    recall_5_scores = []
    recall_10_scores = []
    precision_5_scores = []
    skipped = 0

    for pair in dataset.similar_pairs:
        query_vec = all_embeddings.get(pair.query_source)
        if not query_vec:
            skipped += 1
            continue

        similarities = []
        for citekey, vec in all_embeddings.items():
            if citekey == pair.query_source:
                continue
            sim = cosine_similarity(query_vec, vec)
            similarities.append((citekey, sim))

        similarities.sort(key=lambda x: x[1], reverse=True)
        ranked = [citekey for citekey, _ in similarities]
        relevant = set(pair.relevant)

        recall_5_scores.append(recall_at_k(ranked, relevant, 5))
        recall_10_scores.append(recall_at_k(ranked, relevant, 10))
        precision_5_scores.append(precision_at_k(ranked, relevant, 5))

    evaluated = len(recall_5_scores)

    def avg(scores: list[float]) -> float:
        return round(sum(scores) / len(scores), 4) if scores else 0.0

    return {
        "total_queries": len(dataset.similar_pairs),
        "evaluated": evaluated,
        "skipped": skipped,
        "metrics": {
            "avg_recall_at_5": avg(recall_5_scores),
            "avg_recall_at_10": avg(recall_10_scores),
            "avg_precision_at_5": avg(precision_5_scores),
        },
    }


def build_results_summary(results: dict) -> dict:
    """Flatten headline metrics from a run_all() result dict.

    Returns a flat dict like {"reconstruction.f1": 0.624, "intent.macro_f1": 0.85, ...}
    suitable for quick comparison and storage in benchmark_runs.results_summary.
    """
    summary: dict[str, float] = {}
    if "intent" in results:
        m = results["intent"].get("metrics", {})
        for key in ("macro_f1", "accuracy"):
            if key in m:
                summary[f"intent.{key}"] = m[key]
    if "gaps" in results:
        m = results["gaps"].get("metrics", {})
        for key in ("precision_at_5", "precision_at_10", "ndcg_at_10"):
            if key in m:
                summary[f"gaps.{key}"] = m[key]
    if "embeddings" in results:
        m = results["embeddings"].get("metrics", {})
        for key in ("avg_recall_at_5", "avg_recall_at_10", "avg_precision_at_5"):
            if key in m:
                summary[f"embeddings.{key}"] = m[key]
    if "reconstruction" in results:
        recon = results["reconstruction"]
        bl = recon.get("baseline", {})
        for key in ("source_coverage", "intent_coverage"):
            if key in bl:
                summary[f"reconstruction.baseline.{key}"] = bl[key]
        rc = recon.get("reconstruction", {})
        for key in ("f1", "macro_precision", "macro_recall", "intent_accuracy", "ndcg_avg"):
            if key in rc:
                summary[f"reconstruction.{key}"] = rc[key]
    return summary


def run_all(
    state: StateManager,
    dataset: BenchmarkDataset,
    metrics_filter: str = "all",
    reranked_gaps: list[dict] | None = None,
    ai: object | None = None,
    klemma_home: object | None = None,
) -> dict:
    """Run selected benchmarks and return combined results.

    Multi-format evaluation: each sub-benchmark evaluated independently,
    following SciRepEval methodology (Singh et al. 2023).

    Args:
        reranked_gaps: Pre-ranked gap list for hybrid semantic evaluation.
            Passed through to run_gap_benchmark() when provided.
        ai: AIProvider instance (needed for reconstruction benchmark).
        klemma_home: Path to resolve prompt templates.
    """
    results: dict = {}

    if metrics_filter in ("all", "intent") and dataset.fragments:
        results["intent"] = run_intent_benchmark(state, dataset)

    if metrics_filter in ("all", "gaps") and dataset.gaps:
        results["gaps"] = run_gap_benchmark(state, dataset, reranked_gaps=reranked_gaps)

    if metrics_filter in ("all", "embeddings") and dataset.similar_pairs:
        results["embeddings"] = run_embedding_benchmark(state, dataset)

    if metrics_filter in ("all", "reconstruct") and dataset.reconstruction:
        from .reconstruction import run_reconstruction_benchmark
        results["reconstruction"] = run_reconstruction_benchmark(
            state, dataset.reconstruction, ai=ai, klemma_home=klemma_home,
        )

    return results
