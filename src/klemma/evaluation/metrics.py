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

# Graded relevance for nDCG in reconstruction benchmark
INTENT_RELEVANCE = {"method": 3, "result_comparison": 2, "background": 1}


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


def reconstruction_metrics(
    predictions: list[dict],
    ground_truth: list[dict],
) -> dict:
    """Compute citation reconstruction metrics.

    Each item is a dict with keys: section_id, citekey, intent.

    Metrics:
    - Source precision/recall per section (over citekey sets)
    - Macro precision/recall averaged across sections
    - Intent accuracy for correctly placed (section, citekey) pairs
    - nDCG per section with graded relevance (method=3, result_comparison=2, background=1)
    - Overall F1 (harmonic mean of macro-P and macro-R)
    """
    if not ground_truth:
        return {
            "macro_precision": 0.0,
            "macro_recall": 0.0,
            "f1": 0.0,
            "intent_accuracy": 0.0,
            "ndcg_avg": 0.0,
            "per_section": {},
            "total_gt": 0,
            "total_pred": 0,
        }

    # Group by section
    gt_by_section: dict[str, list[dict]] = defaultdict(list)
    for item in ground_truth:
        gt_by_section[item["section_id"]].append(item)

    pred_by_section: dict[str, list[dict]] = defaultdict(list)
    for item in predictions:
        pred_by_section[item["section_id"]].append(item)

    per_section: dict[str, dict] = {}
    precision_scores = []
    recall_scores = []
    ndcg_scores = []

    # Intent accuracy: count correct intents for matched (section, citekey) pairs
    intent_correct = 0
    intent_total = 0

    for section_id, gt_items in gt_by_section.items():
        gt_citekeys = {item["citekey"] for item in gt_items}
        gt_intent_map = {item["citekey"]: item["intent"] for item in gt_items}

        pred_items = pred_by_section.get(section_id, [])
        pred_citekeys = {item["citekey"] for item in pred_items}
        pred_intent_map = {item["citekey"]: item["intent"] for item in pred_items}

        # Precision / recall over citekey sets
        hits = gt_citekeys & pred_citekeys
        p = len(hits) / len(pred_citekeys) if pred_citekeys else 0.0
        r = len(hits) / len(gt_citekeys) if gt_citekeys else 0.0

        # Intent accuracy for matched pairs
        for ck in hits:
            intent_total += 1
            if pred_intent_map.get(ck) == gt_intent_map.get(ck):
                intent_correct += 1

        # nDCG: build relevance map from ground truth intents
        relevance_map = {
            ck: INTENT_RELEVANCE.get(intent, 1)
            for ck, intent in gt_intent_map.items()
        }
        ranked_pred = [item["citekey"] for item in pred_items]
        section_ndcg = ndcg_at_k(ranked_pred, relevance_map, max(len(relevance_map), 1))

        per_section[section_id] = {
            "precision": round(p, 4),
            "recall": round(r, 4),
            "gt_count": len(gt_citekeys),
            "pred_count": len(pred_citekeys),
            "hits": len(hits),
            "ndcg": section_ndcg,
        }
        precision_scores.append(p)
        recall_scores.append(r)
        ndcg_scores.append(section_ndcg)

    macro_p = sum(precision_scores) / len(precision_scores) if precision_scores else 0.0
    macro_r = sum(recall_scores) / len(recall_scores) if recall_scores else 0.0
    f1 = (2 * macro_p * macro_r / (macro_p + macro_r)) if (macro_p + macro_r) > 0 else 0.0
    intent_acc = intent_correct / intent_total if intent_total > 0 else 0.0
    ndcg_avg = sum(ndcg_scores) / len(ndcg_scores) if ndcg_scores else 0.0

    return {
        "macro_precision": round(macro_p, 4),
        "macro_recall": round(macro_r, 4),
        "f1": round(f1, 4),
        "intent_accuracy": round(intent_acc, 4),
        "ndcg_avg": round(ndcg_avg, 4),
        "per_section": per_section,
        "total_gt": len(ground_truth),
        "total_pred": len(predictions),
    }
