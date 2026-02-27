"""Citation reconstruction benchmark — end-to-end recommendation quality.

Tests whether Klemma can match real-world citation decisions by comparing
two approaches against a paper's actual citation map:
- Baseline: source-coverage check from DB fragments (no AI call)
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
from .pipeline import AblationParams

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
    """Compute source-coverage baseline (section-agnostic).

    For each in-library ground truth citekey, checks whether DB contains
    at least one fragment. No section matching — measures library coverage
    as the prerequisite for any recommendation quality.
    """
    gt_citekeys = {s.citekey for s in dataset.samples}

    # Which GT citekeys have fragments in DB?
    covered: set[str] = set()
    covered_with_intent: dict[str, set[str]] = {}
    for citekey in gt_citekeys:
        frags = state.get_fragments(source_id=citekey, limit=500)
        if frags:
            covered.add(citekey)
            intents = {
                f.get("citation_intent")
                for f in frags
                if f.get("citation_intent")
            }
            covered_with_intent[citekey] = intents

    source_recall = len(covered) / len(gt_citekeys) if gt_citekeys else 0.0

    # Intent coverage: for each GT sample, does DB have a fragment with matching intent?
    intent_hits = 0
    for sample in dataset.samples:
        if sample.citekey in covered_with_intent:
            if sample.intent in covered_with_intent[sample.citekey]:
                intent_hits += 1

    intent_coverage = intent_hits / len(dataset.samples) if dataset.samples else 0.0

    return {
        "method": "baseline",
        "source_coverage": round(source_recall, 4),
        "sources_covered": len(covered),
        "sources_total": len(gt_citekeys),
        "intent_coverage": round(intent_coverage, 4),
    }


def run_reconstruction(
    ai: AIProvider,
    state: StateManager,
    dataset: ReconstructionDataset,
    klemma_home: Optional[Path] = None,
    ablation: Optional[AblationParams] = None,
) -> dict:
    """Run AI-driven citation reconstruction.

    Provides the paper outline and fragment library to AI, which recommends
    citation assignments blind to the actual paper text.

    Args:
        ablation: Override default parameters for ablation experiments.
            Controls temperature, fragments_per_source, max_recs_per_section,
            and prompt variant (few-shot examples).
    """
    from klemma.config import _SHIPPED_PROMPTS_DIR, resolve_prompt

    params = ablation or AblationParams()
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
        frags = state.get_fragments(
            source_id=citekey, limit=params.fragments_per_source,
        )
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
        max_recs_per_section=params.max_recs_per_section,
        examples=params.examples,
    )

    system = (
        "You are a citation recommendation system. "
        "Recommend which sources should be cited in each section. "
        "Output only valid JSON."
    )

    data = ai.call_json(
        system, user_prompt, max_tokens=4096,
        temperature=params.temperature,
    )
    if not data:
        logger.error("Reconstruction prompt failed")
        return {"method": "reconstruction", "error": "AI call failed"}

    # Parse recommendations into prediction dicts
    # Normalize section_ids: AI sometimes returns "I: Introduction" instead of "I"
    gt_section_ids = {s.section_id for s in dataset.ground_truth.sections}
    predictions = []
    seen = set()
    for rec in data.get("recommendations", []):
        section_id = rec.get("section_id", "").strip()
        citekey = rec.get("citekey", "")
        intent = rec.get("intent", "background")
        if not section_id or not citekey:
            continue
        # Normalize: strip title suffix (e.g. "I: Introduction" → "I")
        if section_id not in gt_section_ids and ":" in section_id:
            prefix = section_id.split(":")[0].strip()
            if prefix in gt_section_ids:
                section_id = prefix
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
    ablation: Optional[AblationParams] = None,
) -> dict:
    """Run full reconstruction benchmark: ground truth stats + baseline + optional AI.

    Returns a dict with ground_truth summary, baseline metrics, and
    (if AI provided) reconstruction metrics.

    Args:
        ablation: Override default parameters for ablation experiments.
            Passed through to run_reconstruction().
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
            ai, state, dataset, klemma_home, ablation=ablation,
        )

    return result
