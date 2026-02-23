# Evaluation Framework

Multi-format benchmarking for klemma's intent classification, gap scoring, and embedding retrieval.

## Modules

### dataset.py (~80 lines)
Pydantic models for annotated benchmark datasets + load/export.
- `IntentSample` — fragment with ground truth intent (background/method/result_comparison)
- `GapSample` — reference gap with ground truth relevance (1-5)
- `SimilarityPair` — query source with known relevant neighbors
- `BenchmarkDataset` — container for all three sample types
- `load_dataset(path)` — load and validate from JSON
- `export_dataset(state, path)` — dump current DB as annotation template

### metrics.py (~120 lines)
Pure metric functions — no DB, no IO.
- `intent_metrics(predictions, ground_truth)` — macro-F1 (primary), accuracy, per-class P/R/F1, confusion matrix
- `precision_at_k(ranked_ids, relevant_ids, k)` — fraction of top-K that are relevant
- `recall_at_k(ranked_ids, relevant_ids, k)` — fraction of relevant items in top-K
- `ndcg_at_k(ranked_ids, relevance_map, k)` — normalized DCG for graded relevance (1-5)

### runners.py (~150 lines)
Benchmark runners — orchestrate DB queries + metric computation.
- `run_intent_benchmark(state, dataset)` — compare DB fragment intents vs ground truth
- `run_gap_benchmark(state, dataset)` — evaluate gap scoring precision@K and nDCG@K
- `run_embedding_benchmark(state, dataset)` — evaluate embedding retrieval recall@K
- `run_all(state, dataset, metrics_filter)` — dispatch to selected runners

## Design rationale

Metric and methodology choices grounded in klemma-paper library:

- **Macro-F1 as primary intent metric** (not accuracy): Citation intent classes are imbalanced — background dominates method and result_comparison. Macro-F1 gives equal weight to each class, preventing the classifier from gaming accuracy by predicting the majority class. (Cohan et al. 2019 — SciCite; Subramanian et al. 2021 — LDAM)

- **Multi-format evaluation** (intent + gaps + embeddings evaluated separately): "condensing all relevant information into a single [metric] might not be expressive enough for generalizing across a wide range of tasks" (Singh et al. 2023 — SciRepEval).

- **Precision@K and nDCG@K for gap ranking**: Standard IR metrics for evaluating ranked recommendation lists. F1@20 and MRR used for citation recommendation systems (Bhagavatula et al. 2018). SciDocs uses MAP and nDCG (Cohan et al. 2020 — SPECTER).

- **Recall@K for embedding retrieval**: Embedding quality measured by whether known-relevant documents appear in top-K results. SPECTER evaluated on citation prediction recall (Cohan et al. 2020); SciRepEval proximity tasks use nearest-neighbor retrieval (Singh et al. 2023).

- **Dataset construction from citation sentences**: Following SciFact annotation protocol — natural claims derived from actual citations, not synthetic data (Wadden et al. 2020).

- **JSON output for reproducibility**: "results often remain difficult to replicate due to the lack of access to original datasets" (Kastrin et al. 2025).

- **Expert ground truth requirement**: "A major critique of LBD has been the failure of proposed discoveries to withstand expert assessment" (Henry & McInnes 2017).

## Data flow

```
User creates annotated dataset (JSON)
  or: klemma benchmark --export template.json → review/correct labels
        ↓
klemma benchmark -d dataset.json [--metrics intent|gaps|embeddings|all]
        ↓
runners.py: query DB → compute metrics → return results dict
        ↓
cli.py: Rich table output (default) or JSON (--json-output)
```

## Maintaining this file
Update when: adding new metric functions, changing runner logic, adding new benchmark types, or modifying dataset schema.

See: [Core infrastructure](../CLAUDE.md) | [Tests](../../../tests/CLAUDE.md) | [Root](../../../CLAUDE.md)
