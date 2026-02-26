"""Benchmark dataset schema, loading, and export.

Dataset format follows SciFact annotation protocol (Wadden et al. 2020):
ground truth derived from actual citations, not synthetic data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from klemma.state import StateManager


class IntentSample(BaseModel):
    """A fragment with known citation intent for evaluation."""

    source_id: str
    fragment_text: str
    ground_truth: Literal["background", "method", "result_comparison"]


class GapSample(BaseModel):
    """A reference gap with known relevance for ranking evaluation."""

    ref_title: str
    section: str
    ground_truth_relevance: int = Field(ge=1, le=5)


class SimilarityPair(BaseModel):
    """A query source with known relevant neighbors for retrieval evaluation."""

    query_source: str
    relevant: list[str]


class SectionCitation(BaseModel):
    """A citation within a paper section, with optional library match."""

    citekey: str | None = None
    title: str
    intent: Literal["background", "method", "result_comparison"]
    in_library: bool = False


class PaperSection(BaseModel):
    """A section of a paper with its citations."""

    section_id: str
    title: str
    description: str = ""
    citations: list[SectionCitation] = []


class ReconstructionGroundTruth(BaseModel):
    """Ground truth: paper's actual citation map (sections → cited works)."""

    paper_citekey: str
    paper_title: str
    abstract: str = ""
    keywords: list[str] = []
    sections: list[PaperSection] = []
    bibliography_size: int = 0


class ReconstructionSample(BaseModel):
    """Flattened (section, citekey, intent) triple for evaluation."""

    section_id: str
    citekey: str
    intent: Literal["background", "method", "result_comparison"]


class ReconstructionDataset(BaseModel):
    """Dataset for citation reconstruction benchmark."""

    version: str = "1.0"
    ground_truth: ReconstructionGroundTruth
    samples: list[ReconstructionSample] = []


class BenchmarkDataset(BaseModel):
    """Annotated test set for multi-format evaluation.

    Follows SciRepEval design (Singh et al. 2023): separate sub-benchmarks
    for classification (intent), ranking (gaps), and retrieval (embeddings).
    """

    version: str = "1.0"
    fragments: list[IntentSample] = []
    gaps: list[GapSample] = []
    similar_pairs: list[SimilarityPair] = []
    reconstruction: ReconstructionDataset | None = None


def load_dataset(path: Path) -> BenchmarkDataset:
    """Load and validate an annotated benchmark dataset from JSON."""
    with open(path) as f:
        data = json.load(f)
    return BenchmarkDataset.model_validate(data)


def export_dataset(state: StateManager, path: Path) -> int:
    """Export current DB state as a dataset template for manual annotation.

    Dumps fragments (with DB intents as initial ground_truth) and gaps
    (with DB scores mapped to 1-5 relevance). The user reviews and corrects
    labels to create actual ground truth.

    Returns the number of items exported.
    """
    fragments = []
    for frag in state.get_fragments():
        intent = frag.get("citation_intent")
        if not intent:
            continue
        fragments.append(IntentSample(
            source_id=frag["source_id"],
            fragment_text=frag["fragment_text"][:300],
            ground_truth=intent,
        ).model_dump())

    gaps = []
    for gap in state.get_reference_gaps()[:50]:
        score = gap.get("score", 0)
        relevance = min(5, max(1, round(score / 10) + 1))
        gaps.append(GapSample(
            ref_title=gap.get("ref_title", ""),
            section=gap.get("section", ""),
            ground_truth_relevance=relevance,
        ).model_dump())

    dataset = BenchmarkDataset(
        version="1.0",
        fragments=[IntentSample.model_validate(f) for f in fragments[:50]],
        gaps=[GapSample.model_validate(g) for g in gaps[:20]],
        similar_pairs=[],
    )

    with open(path, "w") as f:
        json.dump(dataset.model_dump(), f, indent=2)

    return len(dataset.fragments) + len(dataset.gaps)
