"""Pure metric functions for multi-format evaluation.

No DB, no IO — stateless computation only.

Design rationale:
- Macro-F1 as primary intent metric (Cohan et al. 2019; Subramanian et al. 2021)
- Precision@K / nDCG@K for ranking (Bhagavatula et al. 2018; Cohan et al. 2020)
- Recall@K for retrieval (Singh et al. 2023; Cohan et al. 2020)
"""

from __future__ import annotations

import math
from collections import defaultdict

INTENT_CLASSES = ("background", "method", "result_comparison")


def intent_metrics(
    predictions: list[str], ground_truth: list[str]
) -> dict:
    """Compute intent classification metrics.

    Primary metric: macro_f1 — gives equal weight to each intent class,
    preventing majority-class bias when background >> method >> result_comparison
    (Cohan et al. 2019 — SciCite; Subramanian et al. 2021 — LDAM).

    Returns:
        {macro_f1, accuracy, per_class: {cls: {precision, recall, f1, support}},
         confusion: {predicted: {actual: count}}, total}
    """
    if not predictions or not ground_truth:
        return {
            "macro_f1": 0.0,
            "accuracy": 0.0,
            "per_class": {},
            "confusion": {},
            "total": 0,
        }

    n = min(len(predictions), len(ground_truth))

    # Confusion matrix: confusion[predicted][actual] = count
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    correct = 0
    for i in range(n):
        pred, actual = predictions[i], ground_truth[i]
        confusion[pred][actual] += 1
        if pred == actual:
            correct += 1

    # Per-class precision/recall/F1
    per_class: dict[str, dict] = {}
    f1_scores = []

    for cls in INTENT_CLASSES:
        tp = confusion.get(cls, {}).get(cls, 0)
        fp = sum(confusion.get(cls, {}).get(a, 0) for a in INTENT_CLASSES if a != cls)
        fn = sum(confusion.get(p, {}).get(cls, 0) for p in INTENT_CLASSES if p != cls)
        support = tp + fn

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        per_class[cls] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": support,
        }
        if support > 0:
            f1_scores.append(f1)

    macro_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0

    return {
        "macro_f1": round(macro_f1, 4),
        "accuracy": round(correct / n, 4) if n else 0.0,
        "per_class": per_class,
        "confusion": {p: dict(acts) for p, acts in confusion.items()},
        "total": n,
    }


def precision_at_k(
    ranked_ids: list[str], relevant_ids: set[str], k: int
) -> float:
    """Precision@K — fraction of top-K that are relevant.

    Standard IR metric for ranked recommendation lists
    (Bhagavatula et al. 2018 — citation recommendation; Cohan et al. 2020 — SciDocs).
    """
    if k <= 0 or not relevant_ids:
        return 0.0
    top_k = ranked_ids[:k]
    hits = sum(1 for item in top_k if item in relevant_ids)
    return round(hits / min(k, len(ranked_ids)), 4) if ranked_ids else 0.0


def recall_at_k(
    ranked_ids: list[str], relevant_ids: set[str], k: int
) -> float:
    """Recall@K — fraction of relevant items found in top-K.

    Embedding quality measured by retrieval of known-relevant documents
    (Singh et al. 2023 — SciRepEval proximity tasks; Cohan et al. 2020 — SPECTER).
    """
    if k <= 0 or not relevant_ids:
        return 0.0
    top_k = ranked_ids[:k]
    hits = sum(1 for item in top_k if item in relevant_ids)
    return round(hits / len(relevant_ids), 4)


def ndcg_at_k(
    ranked_ids: list[str], relevance_map: dict[str, int], k: int
) -> float:
    """Normalized Discounted Cumulative Gain at K for graded relevance (1-5).

    nDCG accounts for position in ranking — highly relevant items should
    appear earlier. Used in SciDocs (Cohan et al. 2020) and SciRepEval
    (Singh et al. 2023) for retrieval evaluation.
    """
    if k <= 0 or not relevance_map:
        return 0.0

    # DCG of actual ranking
    dcg = 0.0
    for i, item_id in enumerate(ranked_ids[:k]):
        rel = relevance_map.get(item_id, 0)
        dcg += (2**rel - 1) / math.log2(i + 2)

    # Ideal DCG — sort by relevance descending
    ideal_rels = sorted(relevance_map.values(), reverse=True)[:k]
    idcg = 0.0
    for i, rel in enumerate(ideal_rels):
        idcg += (2**rel - 1) / math.log2(i + 2)

    return round(dcg / idcg, 4) if idcg > 0 else 0.0
