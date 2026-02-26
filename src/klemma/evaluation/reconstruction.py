"""Citation reconstruction benchmark — end-to-end recommendation quality.

Tests whether Klemma can match real-world citation decisions by comparing
two approaches against a paper's actual citation map:
- Baseline: existing DB fragment assignments (no AI call)
- Reconstruction: AI prompt assigns citations to a test paper's sections

Design: take a published paper as ground truth. An analyst prompt extracts
its outline + citation map. Klemma then tries to reconstruct citation
assignments using only the outline + fragment library (blind to paper text).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .dataset import ReconstructionDataset, ReconstructionGroundTruth
from .metrics import reconstruction_metrics

if TYPE_CHECKING:
    from klemma.ai import AIProvider
    from klemma.state import StateManager

logger = logging.getLogger(__name__)


def run_analyst(
    ai: AIProvider,
    pdf_text: str,
    library_entries: str,
    paper_citekey: str,
    paper_title: str,
    klemma_home: Optional[Path] = None,
) -> Optional[ReconstructionGroundTruth]:
    """Run analyst prompt on a paper to extract ground truth citation map.

    Args:
        ai: AI provider for the analyst call.
        pdf_text: Full text of the paper.
        library_entries: Formatted library entries for matching.
        paper_citekey: Citekey of the paper being analyzed.
        paper_title: Title of the paper.
        klemma_home: Path to resolve prompt templates.

    Returns:
        ReconstructionGroundTruth or None on failure.
    """
    from klemma.config import _SHIPPED_PROMPTS_DIR, resolve_prompt

    prompt_path = (
        resolve_prompt("analyst.md", klemma_home)
        if klemma_home
        else _SHIPPED_PROMPTS_DIR / "analyst.md"
    )
    user_prompt = ai.render_prompt(
        prompt_path,
        pdf_text=pdf_text,
        library_entries=library_entries,
        paper_citekey=paper_citekey,
        paper_title=paper_title,
    )

    system = (
        "You are a research analyst extracting the citation map from a scientific paper. "
        "Output only valid JSON."
    )

    data = ai.call_json(system, user_prompt, max_tokens=8192)
    if not data:
        logger.error("Analyst prompt failed for %s", paper_citekey)
        return None

    try:
        return ReconstructionGroundTruth.model_validate(data)
    except Exception as e:
        logger.error("Failed to parse analyst response: %s", e)
        return None


def compute_baseline(
    state: StateManager,
    dataset: ReconstructionDataset,
) -> dict:
    """Compute baseline metrics using existing DB fragment assignments.

    For each ground truth sample, checks if DB has a fragment from that
    source assigned to a matching section. No AI call needed.
    """
    predictions = []

    # Get all fragments from DB, grouped by source
    gt_citekeys = {s.citekey for s in dataset.samples}
    for citekey in gt_citekeys:
        frags = state.get_fragments(source_id=citekey, limit=500)
        for frag in frags:
            section = frag.get("section", "")
            intent = frag.get("citation_intent")
            if section and intent:
                predictions.append({
                    "section_id": section,
                    "citekey": citekey,
                    "intent": intent,
                })

    # Deduplicate: keep first occurrence of (section_id, citekey)
    seen = set()
    unique_preds = []
    for p in predictions:
        key = (p["section_id"], p["citekey"])
        if key not in seen:
            seen.add(key)
            unique_preds.append(p)

    gt_dicts = [s.model_dump() for s in dataset.samples]
    metrics = reconstruction_metrics(unique_preds, gt_dicts)

    return {
        "method": "baseline",
        "predictions_count": len(unique_preds),
        **metrics,
    }


def run_reconstruction(
    ai: AIProvider,
    state: StateManager,
    dataset: ReconstructionDataset,
    klemma_home: Optional[Path] = None,
) -> dict:
    """Run AI-driven citation reconstruction.

    Provides the paper outline and fragment library to AI, which recommends
    citation assignments blind to the actual paper text.
    """
    from klemma.config import _SHIPPED_PROMPTS_DIR, resolve_prompt

    gt = dataset.ground_truth

    # Build section list with descriptions from ground truth
    sections = [
        {
            "section_id": s.section_id,
            "title": s.title,
            "description": s.description,
        }
        for s in gt.sections
    ]

    # Build source context: citekeys with their top fragments + abstracts
    gt_citekeys = {s.citekey for s in dataset.samples}
    sources = []
    for citekey in gt_citekeys:
        source_info = state.get_source(citekey)
        frags = state.get_fragments(source_id=citekey, limit=5)
        sources.append({
            "citekey": citekey,
            "title": source_info.get("title", "") if source_info else "",
            "year": source_info.get("year", "") if source_info else "",
            "abstract": source_info.get("abstract", "") if source_info else "",
            "fragments": [
                {
                    "text": f.get("fragment_text", ""),
                    "intent": f.get("citation_intent", "background"),
                }
                for f in frags
            ],
        })

    prompt_path = (
        resolve_prompt("reconstruct.md", klemma_home)
        if klemma_home
        else _SHIPPED_PROMPTS_DIR / "reconstruct.md"
    )
    user_prompt = ai.render_prompt(
        prompt_path,
        paper_title=gt.paper_title,
        abstract=gt.abstract,
        keywords=gt.keywords,
        sections=sections,
        sources=sources,
    )

    system = (
        "You are a citation recommendation system. "
        "Recommend which sources should be cited in each section. "
        "Output only valid JSON."
    )

    data = ai.call_json(system, user_prompt, max_tokens=4096)
    if not data:
        logger.error("Reconstruction prompt failed")
        return {"method": "reconstruction", "error": "AI call failed"}

    # Parse recommendations into prediction dicts
    predictions = []
    seen = set()
    for rec in data.get("recommendations", []):
        section_id = rec.get("section_id", "")
        citekey = rec.get("citekey", "")
        intent = rec.get("intent", "background")
        if not section_id or not citekey:
            continue
        key = (section_id, citekey)
        if key not in seen:
            seen.add(key)
            predictions.append({
                "section_id": section_id,
                "citekey": citekey,
                "intent": intent,
            })

    gt_dicts = [s.model_dump() for s in dataset.samples]
    metrics = reconstruction_metrics(predictions, gt_dicts)

    return {
        "method": "reconstruction",
        "predictions_count": len(predictions),
        **metrics,
    }


def run_reconstruction_benchmark(
    state: StateManager,
    dataset: ReconstructionDataset,
    ai: Optional[AIProvider] = None,
    klemma_home: Optional[Path] = None,
) -> dict:
    """Run full reconstruction benchmark: ground truth stats + baseline + optional AI.

    Returns a dict with ground_truth summary, baseline metrics, and
    (if AI provided) reconstruction metrics.
    """
    gt = dataset.ground_truth
    in_library = sum(
        1
        for section in gt.sections
        for c in section.citations
        if c.in_library
    )

    result: dict = {
        "ground_truth": {
            "paper": gt.paper_citekey,
            "sections": len(gt.sections),
            "bibliography_size": gt.bibliography_size,
            "in_library_citations": in_library,
            "samples": len(dataset.samples),
        },
        "baseline": compute_baseline(state, dataset),
    }

    if ai:
        result["reconstruction"] = run_reconstruction(
            ai, state, dataset, klemma_home
        )

    return result
