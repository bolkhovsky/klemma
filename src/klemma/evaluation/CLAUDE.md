# Evaluation Framework

Multi-format benchmarking for klemma's intent classification, gap scoring, embedding retrieval, and citation reconstruction.

## Modules

### dataset.py (~130 lines)
Pydantic models for annotated benchmark datasets + load/export.
- `IntentSample` — fragment with ground truth intent (background/method/result_comparison)
- `GapSample` — reference gap with ground truth relevance (1-5)
- `SimilarityPair` — query source with known relevant neighbors
- `SectionCitation` — a citation within a paper section, with optional library match
- `PaperSection` — a section of a paper with its citations
- `ReconstructionGroundTruth` — paper's actual citation map (sections → cited works)
- `ReconstructionSample` — flattened (section, citekey, intent) triple for evaluation
- `ReconstructionDataset` — ground truth + samples for reconstruction benchmark
- `BenchmarkDataset` — container for all sample types (+ optional `reconstruction`)
- `load_dataset(path)` — load and validate from JSON
- `export_dataset(state, path)` — dump current DB as annotation template

### metrics.py (~210 lines)
Pure metric functions — no DB, no IO.
- `intent_metrics(predictions, ground_truth)` — macro-F1 (primary), accuracy, per-class P/R/F1, confusion matrix
- `precision_at_k(ranked_ids, relevant_ids, k)` — fraction of top-K that are relevant
- `recall_at_k(ranked_ids, relevant_ids, k)` — fraction of relevant items in top-K
- `ndcg_at_k(ranked_ids, relevance_map, k)` — normalized DCG for graded relevance (1-5)
- `reconstruction_metrics(predictions, ground_truth)` — macro P/R, F1, intent accuracy, nDCG per section for citation reconstruction

### runners.py (~200 lines)
Benchmark runners — orchestrate DB queries + metric computation.
- `run_intent_benchmark(state, dataset)` — compare DB fragment intents vs ground truth
- `run_gap_benchmark(state, dataset, reranked_gaps?)` — evaluate gap scoring precision@K and nDCG@K; when `reranked_gaps` is provided, skips DB fetch and uses the pre-ranked list (enables hybrid semantic evaluation)
- `run_embedding_benchmark(state, dataset)` — evaluate embedding retrieval recall@K
- `run_all(state, dataset, metrics_filter, reranked_gaps?, ai?, klemma_home?)` — dispatch to selected runners; includes reconstruction when `metrics_filter` is `"all"` or `"reconstruct"`

### reconstruction.py (~200 lines)
Citation reconstruction benchmark — end-to-end recommendation quality.
- `run_analyst(ai, pdf_text, library_entries, paper_citekey, paper_title, klemma_home)` — extract ground truth citation map from a paper's PDF via AI
- `compute_baseline(state, dataset)` — evaluate DB-only fragment assignments against ground truth
- `run_reconstruction(ai, state, dataset, klemma_home)` — AI-driven citation recommendation against ground truth
- `run_reconstruction_benchmark(state, dataset, ai?, klemma_home?)` — full benchmark: ground truth stats + baseline + optional AI reconstruction

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
  or: klemma benchmark --analyst <citekey> → AI extracts ground truth from paper PDF
        ↓
klemma benchmark -d dataset.json [--metrics intent|gaps|embeddings|reconstruct|all] [--semantic] [--reconstruct]
        ↓
--semantic: state.rerank_gaps_semantic(all_gaps, embeddings) → reranked_gaps
--reconstruct: run_reconstruction_benchmark(state, dataset, ai, klemma_home)
        ↓
runners.py: query DB (or use reranked_gaps) → compute metrics → return results dict
        ↓
cli.py: Rich table output (default) or JSON (--json-output)
```

### Citation reconstruction flow

```
klemma benchmark --analyst <citekey>
  → PDFExtractor.extract(pdf) → library entries → AI analyst prompt → ReconstructionGroundTruth
  → build ReconstructionSample list (in-library only) → save as JSON

klemma benchmark -d dataset.json --reconstruct
  → compute_baseline(state, dataset) → DB fragment assignments → reconstruction_metrics
  → run_reconstruction(ai, state, dataset) → AI prompt with outline + fragments → reconstruction_metrics
  → compare baseline vs reconstruction
```

### Semantic reranking in status/library

`_print_ref_gaps_table(state, limit, embeddings?)` — when embeddings provided, calls
`state.rerank_gaps_semantic()` before display and shows `(semantically reranked)` in title.
Triggered automatically when embeddings are configured in `status --verbose` and `library`.

## Maintaining this file
Update when: adding new metric functions, changing runner logic, adding new benchmark types, or modifying dataset schema.

See: [Core infrastructure](../CLAUDE.md) | [Tests](../../../tests/CLAUDE.md) | [Root](../../../CLAUDE.md)
