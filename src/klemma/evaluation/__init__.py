"""Evaluation framework for klemma — multi-format benchmarking."""

from .dataset import (
    BenchmarkDataset,
    GapSample,
    IntentSample,
    ReconstructionDataset,
    ReconstructionGroundTruth,
    ReconstructionSample,
    SimilarityPair,
    export_dataset,
    load_dataset,
)
from .metrics import intent_metrics, ndcg_at_k, precision_at_k, recall_at_k, reconstruction_metrics
from .runners import (
    build_results_summary,
    run_all,
    run_embedding_benchmark,
    run_gap_benchmark,
    run_intent_benchmark,
)

__all__ = [
    "BenchmarkDataset",
    "GapSample",
    "IntentSample",
    "ReconstructionDataset",
    "ReconstructionGroundTruth",
    "ReconstructionSample",
    "SimilarityPair",
    "export_dataset",
    "intent_metrics",
    "load_dataset",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
    "reconstruction_metrics",
    "build_results_summary",
    "run_all",
    "run_embedding_benchmark",
    "run_gap_benchmark",
    "run_intent_benchmark",
]
