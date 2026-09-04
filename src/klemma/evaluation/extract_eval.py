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
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..text_normalize import normalize

MATCH_OVERLAP = 0.8
_PAGE_MARKER_RE = re.compile(r"\[Page \d+\]\n?")


class GoldError(ValueError):
    """A gold file is malformed — never turn that into a passing score."""


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
    candidates: dict[str, str] = field(default_factory=dict)  # text_key → text (for labelling)

    @property
    def recall(self) -> float:
        return self.found / self.total if self.total else 0.0

    @property
    def label_coverage(self) -> float:
        return self.labelled / self.fragments if self.fragments else 1.0

    @property
    def precision(self) -> Optional[float]:
        """Only defined when EVERY fragment of the run is labelled — an
        unlabelled fragment is not silently dropped from the denominator."""
        if not self.fragments or self.labelled < self.fragments:
            return None
        return self.relevant / self.labelled


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
        claims = data.get("claims")
        if not isinstance(claims, list) or not claims:
            raise GoldError(f"{path.name}: no claims — an empty frame cannot be evaluated")
        bad = [i for i, c in enumerate(claims) if not isinstance(c, dict) or not str(c.get("quote", "")).strip()]
        if bad:
            raise GoldError(f"{path.name}: claims without a quote at positions {bad}")
        docs.append(GoldDoc(
            citekey=data.get("citekey") or path.stem,
            frame_pages=(int(fp[0]), int(fp[1])),
            claims=claims,
            labels=labels,
        ))
    return docs


def manifest(gold_dir: Path) -> dict:
    """Names + sha256 of the gold files — the only thing that goes into git."""
    entries = []
    for path in sorted(gold_dir.glob("*.json")):
        entries.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {"gold_dir": str(gold_dir), "files": entries}


def _strip_markers(frame_text: str) -> tuple[str, list[int]]:
    """Frame text without ``[Page N]`` markers plus a map stripped→raw index."""
    out: list[str] = []
    idx: list[int] = []
    pos = 0
    for m in _PAGE_MARKER_RE.finditer(frame_text):
        for i in range(pos, m.start()):
            out.append(frame_text[i])
            idx.append(i)
        pos = m.end()
    for i in range(pos, len(frame_text)):
        out.append(frame_text[i])
        idx.append(i)
    return "".join(out), idx


def gold_span(claim: dict, frame_text: str) -> Optional[tuple[int, int]]:
    """Locate the gold quote in RAW frame coordinates (the engine's span
    system), tolerating a page marker inside the quote."""
    from ..skills.extract_engine import locate_fragment_span

    quote = str(claim.get("quote", "")).strip()
    if not quote:
        return None
    hit = locate_fragment_span(quote, frame_text)
    if hit:
        return hit
    stripped, idx = _strip_markers(frame_text)
    hit = locate_fragment_span(quote, stripped)
    if not hit or not idx:
        return None
    return idx[hit[0]], idx[min(hit[1], len(idx)) - 1] + 1


def claim_found(
    claim: dict, fragments: list[tuple[str, Optional[tuple[int, int]]]], frame_text: str,
) -> bool:
    """Containment of the normalized quote OR ≥ 80 % overlap with the gold span
    (both spans in raw frame coordinates)."""
    quote_norm = normalize(str(claim.get("quote", "")))
    if not quote_norm:
        return False
    for text, _span in fragments:
        if quote_norm in normalize(text):
            return True
    gspan = gold_span(claim, frame_text)
    if gspan is None:
        return False
    need = MATCH_OVERLAP * (gspan[1] - gspan[0])
    for _text, span in fragments:
        if not span:
            continue
        overlap = min(span[1], gspan[1]) - max(span[0], gspan[0])
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
    candidates: dict[str, str] = {}
    for text, _ in fragments:
        key = text_key(text)
        candidates[key] = text
        label = doc.labels.get(key)
        if label:
            labelled += 1
            if label == "relevant":
                relevant += 1
    return RunMetrics(run_index=run_index, found=found, total=len(doc.claims),
                      labelled=labelled, relevant=relevant, fragments=len(fragments),
                      candidates=candidates)


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


@dataclass
class Verdict:
    min_recall: float
    min_precision: Optional[float]
    recall_pass: bool
    precision_pass: Optional[bool]  # None = not evaluated (labels incomplete)

    @property
    def passed(self) -> bool:
        return self.recall_pass and self.precision_pass is not False


def verdict(
    results: list[DocResult], *, recall_threshold: float = 0.9, precision_threshold: float = 0.8,
) -> Verdict:
    min_recalls = [r.min_recall for r in results]
    precs = [m.precision for r in results for m in r.runs]
    complete = bool(precs) and all(p is not None for p in precs)
    min_p = min(precs) if complete else None
    mr = min(min_recalls) if min_recalls else 0.0
    return Verdict(
        min_recall=mr, min_precision=min_p, recall_pass=mr >= recall_threshold,
        precision_pass=(min_p >= precision_threshold) if min_p is not None else None,
    )


def candidate_labels_template(results: list[DocResult]) -> dict[str, dict[str, str]]:
    """Per citekey: text_key → fragment text of every scored fragment, so the
    author can produce ``<citekey>.labels.json`` for the fragments that were
    actually extracted (they are not deterministic across runs)."""
    out: dict[str, dict[str, str]] = {}
    for r in results:
        merged: dict[str, str] = {}
        for m in r.runs:
            merged.update(m.candidates)
        out[r.citekey] = merged
    return out


def render_report(
    results: list[DocResult], *, identity: dict, recall_threshold: float = 0.9,
    precision_threshold: float = 0.8,
) -> str:
    """Markdown report: metrics and hashes only, never quotes."""
    lines = ["# Exhaustive extraction eval", ""]
    for k, v in identity.items():
        lines.append(f"- {k}: `{v}`")
    lines += ["", "| citekey | claims | min recall | mean recall | min precision | pooled precision | label coverage | frags/run |",
              "|---|---|---|---|---|---|---|---|"]
    for r in results:
        precs = [m.precision for m in r.runs]
        pooled_l = sum(m.labelled for m in r.runs)
        pooled_r = sum(m.relevant for m in r.runs)
        pooled = pooled_r / pooled_l if pooled_l else None
        min_p = min(precs) if precs and all(p is not None for p in precs) else None
        cov = min(m.label_coverage for m in r.runs)
        frags = "/".join(str(m.fragments) for m in r.runs)
        lines.append(
            f"| {r.citekey} | {r.runs[0].total} | {r.min_recall:.2f} | {r.mean_recall:.2f} | "
            f"{'—' if min_p is None else f'{min_p:.2f}'} | {'—' if pooled is None else f'{pooled:.2f}'} | "
            f"{cov:.2f} | {frags} |"
        )
    v = verdict(results, recall_threshold=recall_threshold, precision_threshold=precision_threshold)
    lines += ["", f"Acceptance recall: min {v.min_recall:.2f} (threshold {recall_threshold:.2f}) → "
              f"{'pass' if v.recall_pass else 'fail'}"]
    if v.min_precision is not None:
        lines.append(f"Acceptance precision: min {v.min_precision:.2f} (threshold {precision_threshold:.2f}) → "
                     f"{'pass' if v.precision_pass else 'fail'}")
    else:
        lines.append("Acceptance precision: not evaluated — label every scored fragment "
                     "(`<citekey>.labels.json`, keys from the candidates file) to enable it.")
    return "\n".join(lines) + "\n"
