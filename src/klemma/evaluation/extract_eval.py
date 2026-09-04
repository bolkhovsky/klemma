"""Gold-frame evaluation of the exhaustive extraction mode (plan C4).

Recall is measured on an *exhaustively annotated frame*: for each gold
document the author picks a bounded page range and lists EVERY claim in it
that relates to the outline. A gold claim counts as found when an extracted
fragment, after NFKC normalisation, contains the gold quote entirely, or
when the fragment span overlaps the gold span by at least 80 % of the gold
length. Precision cannot be measured against an incomplete annotation, so it
comes from manual labels of the extracted fragments (relevant / irrelevant /
not_verbatim) and is reported per run; the threshold applies to the minimum
over runs, mean and pooled values are informational.

Gold files live OUTSIDE git (they quote copyrighted papers); the repository
keeps only a manifest with hashes. Format of ``<citekey>.json``::

    {"citekey": "gost2025", "frame_pages": [3, 8],
     "claims": [{"quote": "...", "item": "2.4.1"}, ...]}

Optional ``<citekey>.labels.json``: ``{"<sha256 of normalized fragment text>":
"relevant" | "irrelevant" | "not_verbatim"}``.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..text_normalize import normalize

MATCH_OVERLAP = 0.8


@dataclass
class GoldDoc:
    citekey: str
    frame_pages: tuple[int, int]
    claims: list[dict]
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class RunMetrics:
    run_index: int
    found: int
    total: int
    labelled: int = 0
    relevant: int = 0
    fragments: int = 0

    @property
    def recall(self) -> float:
        return self.found / self.total if self.total else 1.0

    @property
    def precision(self) -> Optional[float]:
        return self.relevant / self.labelled if self.labelled else None


@dataclass
class DocResult:
    citekey: str
    runs: list[RunMetrics]

    @property
    def min_recall(self) -> float:
        return min(r.recall for r in self.runs)

    @property
    def mean_recall(self) -> float:
        return statistics.fmean(r.recall for r in self.runs)


def text_key(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def load_gold_dir(gold_dir: Path) -> list[GoldDoc]:
    docs: list[GoldDoc] = []
    for path in sorted(gold_dir.glob("*.json")):
        if path.name.endswith(".labels.json"):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        labels_path = path.with_name(path.stem + ".labels.json")
        labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
        fp = data.get("frame_pages") or [1, 1]
        docs.append(GoldDoc(
            citekey=data.get("citekey") or path.stem,
            frame_pages=(int(fp[0]), int(fp[1])),
            claims=[c for c in data.get("claims", []) if c.get("quote")],
            labels=labels,
        ))
    return docs


def manifest(gold_dir: Path) -> dict:
    """Names + sha256 of the gold files — the only thing that goes into git."""
    entries = []
    for path in sorted(gold_dir.glob("*.json")):
        entries.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {"gold_dir": str(gold_dir), "files": entries}


def claim_found(
    claim: dict, fragments: list[tuple[str, Optional[tuple[int, int]]]], frame_text: str,
) -> bool:
    """Containment of the normalized quote OR ≥ 80 % overlap with the gold span."""
    quote_norm = normalize(claim["quote"])
    if not quote_norm:
        return False
    for text, _span in fragments:
        if quote_norm in normalize(text):
            return True
    gold_pos = normalize(frame_text).find(quote_norm)
    if gold_pos < 0:
        return False
    gold_span = (gold_pos, gold_pos + len(quote_norm))
    need = MATCH_OVERLAP * (gold_span[1] - gold_span[0])
    for _text, span in fragments:
        if not span:
            continue
        overlap = min(span[1], gold_span[1]) - max(span[0], gold_span[0])
        if overlap >= need:
            return True
    return False


def score_run(
    run_index: int,
    doc: GoldDoc,
    fragments: list[tuple[str, Optional[tuple[int, int]]]],
    frame_text: str,
) -> RunMetrics:
    found = sum(1 for c in doc.claims if claim_found(c, fragments, frame_text))
    labelled = relevant = 0
    for text, _ in fragments:
        label = doc.labels.get(text_key(text))
        if label:
            labelled += 1
            if label == "relevant":
                relevant += 1
    return RunMetrics(run_index=run_index, found=found, total=len(doc.claims),
                      labelled=labelled, relevant=relevant, fragments=len(fragments))


def evaluate(
    docs: list[GoldDoc],
    runner: Callable[[GoldDoc, int], tuple[list[tuple[str, Optional[tuple[int, int]]]], str]],
    *,
    runs: int = 3,
) -> list[DocResult]:
    """``runner(doc, run_index)`` returns (fragments as (text, span-in-frame), frame_text)."""
    results: list[DocResult] = []
    for doc in docs:
        metrics = []
        for i in range(runs):
            fragments, frame_text = runner(doc, i)
            metrics.append(score_run(i, doc, fragments, frame_text))
        results.append(DocResult(citekey=doc.citekey, runs=metrics))
    return results


def render_report(
    results: list[DocResult], *, identity: dict, recall_threshold: float = 0.9,
    precision_threshold: float = 0.8,
) -> str:
    """Markdown report: metrics and hashes only, never quotes."""
    lines = ["# Exhaustive extraction eval", ""]
    for k, v in identity.items():
        lines.append(f"- {k}: `{v}`")
    lines += ["", "| citekey | claims | min recall | mean recall | min precision | pooled precision | frags/run |",
              "|---|---|---|---|---|---|---|"]
    min_recalls, min_precisions = [], []
    for r in results:
        precs = [m.precision for m in r.runs if m.precision is not None]
        pooled_l = sum(m.labelled for m in r.runs)
        pooled_r = sum(m.relevant for m in r.runs)
        pooled = pooled_r / pooled_l if pooled_l else None
        min_p = min(precs) if precs else None
        min_recalls.append(r.min_recall)
        if min_p is not None:
            min_precisions.append(min_p)
        frags = "/".join(str(m.fragments) for m in r.runs)
        lines.append(
            f"| {r.citekey} | {r.runs[0].total} | {r.min_recall:.2f} | {r.mean_recall:.2f} | "
            f"{'—' if min_p is None else f'{min_p:.2f}'} | {'—' if pooled is None else f'{pooled:.2f}'} | {frags} |"
        )
    overall_recall = min(min_recalls) if min_recalls else 1.0
    overall_prec = min(min_precisions) if min_precisions else None
    lines += ["", f"Acceptance: min recall {overall_recall:.2f} (threshold {recall_threshold:.2f}) → "
              f"{'PASS' if overall_recall >= recall_threshold else 'FAIL'}"]
    if overall_prec is not None:
        lines.append(f"Acceptance: min precision {overall_prec:.2f} (threshold {precision_threshold:.2f}) → "
                     f"{'PASS' if overall_prec >= precision_threshold else 'FAIL'}")
    else:
        lines.append("Precision: no manual labels supplied (`<citekey>.labels.json`) — not evaluated.")
    return "\n".join(lines) + "\n"
