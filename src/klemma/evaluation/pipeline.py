"""Full autonomous benchmark pipeline.

Composes: candidate selection → prepare (fetch missing refs) →
analyst (extract ground truth) → benchmark → persist → compare.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

from .prepare import PrepareResult

if TYPE_CHECKING:
    from klemma.ai import AIProvider
    from klemma.config import KlemmaConfig
    from klemma.state import StateManager

logger = logging.getLogger(__name__)


def compute_prompt_hash(prompt_name: str, klemma_home: Optional[Path] = None) -> str:
    """Compute SHA-256 prefix of a prompt template file.

    Returns first 12 hex chars — enough to detect template changes between runs.
    """
    from klemma.config import _SHIPPED_PROMPTS_DIR, resolve_prompt

    prompt_path = (
        resolve_prompt(prompt_name, klemma_home)
        if klemma_home
        else _SHIPPED_PROMPTS_DIR / prompt_name
    )
    try:
        content = prompt_path.read_bytes()
        return hashlib.sha256(content).hexdigest()[:12]
    except (OSError, FileNotFoundError):
        return ""


class AblationParams(BaseModel):
    """Overridable parameters for ablation experiments (Issue #42).

    Defaults match current behavior so passing AblationParams() changes nothing.
    """
    temperature: float = 0.2
    max_recs_per_section: Optional[int] = None  # None = uncapped (current)
    fragments_per_source: int = 5
    prompt_variant: str = "default"  # "default" | "fewshot"

    # Few-shot golden examples (populated when prompt_variant == "fewshot")
    examples: list[dict] = []

    def to_snapshot(self) -> dict:
        """Serialize for config_snapshot storage."""
        return {
            "temperature": self.temperature,
            "max_recs_per_section": self.max_recs_per_section,
            "fragments_per_source": self.fragments_per_source,
            "prompt_variant": self.prompt_variant,
        }

    @classmethod
    def with_fewshot(cls, **kwargs) -> AblationParams:
        """Create params with built-in few-shot golden examples."""
        examples = [
            {
                "section_id": "2.1",
                "section_title": "Citation Intent Classification",
                "citekey": "cohan2019",
                "intent": "method",
                "justification": "SciCite provides the 3-class intent taxonomy "
                "(background/method/result_comparison) used in this work",
            },
            {
                "section_id": "3.2",
                "section_title": "Evaluation Metrics",
                "citekey": "singh2023",
                "intent": "method",
                "justification": "SciRepEval's multi-format evaluation design "
                "justifies separate metrics per task type",
            },
        ]
        return cls(prompt_variant="fewshot", examples=examples, **kwargs)


class AutoBenchmarkResult(BaseModel):
    paper_citekey: str
    prepare_result: Optional[PrepareResult] = None
    results: dict = {}
    run_id: str = ""
    previous_run_id: Optional[str] = None
    comparison: Optional[dict] = None


def run_analyst_from_source(
    state: StateManager,
    ai: AIProvider,
    citekey: str,
    config: KlemmaConfig,
    klemma_home: Optional[Path] = None,
):
    """Extract ground truth from a paper's PDF. Returns ReconstructionDataset or None.

    Shared by --analyst CLI and --auto pipeline.
    """
    from klemma.literature.pdf import PDFExtractor

    from .dataset import ReconstructionDataset, ReconstructionSample
    from .reconstruction import run_analyst

    source = state.get_source(citekey)
    if not source:
        logger.error("Source %s not found", citekey)
        return None

    # Find PDF
    pdf_path = None
    if source.get("pdf_path"):
        pdf_path = Path(source["pdf_path"])
    elif config.zotero.library_json:
        lookup = PDFExtractor.load_pdf_lookup(Path(config.zotero.library_json))
        if citekey in lookup:
            pdf_path = Path(lookup[citekey])

    if not pdf_path or not pdf_path.exists():
        logger.error("PDF not found for %s", citekey)
        return None

    extractor = PDFExtractor(max_chars=config.ai.max_pdf_chars)
    pdf_text = extractor.extract(pdf_path)
    if not pdf_text:
        logger.error("PDF extraction failed for %s", citekey)
        return None

    # Build library entries
    all_sources = state.get_all_sources()
    library_lines = [
        f"- {s.get('id', '')}: {s.get('title', '')}"
        for s in all_sources if s.get("id") != citekey
    ]
    library_entries = "\n".join(library_lines)

    gt = run_analyst(
        ai, pdf_text, library_entries,
        paper_citekey=citekey,
        paper_title=source.get("title", ""),
        klemma_home=klemma_home,
    )
    if not gt:
        return None

    samples = []
    for section in gt.sections:
        for cit in section.citations:
            if cit.in_library and cit.citekey:
                samples.append(ReconstructionSample(
                    section_id=section.section_id,
                    citekey=cit.citekey,
                    intent=cit.intent,
                ))

    return ReconstructionDataset(ground_truth=gt, samples=samples)


def run_auto_benchmark(
    state: StateManager,
    ai: AIProvider,
    config: KlemmaConfig,
    klemma_home: Optional[Path] = None,
    paper_citekey: Optional[str] = None,
    skip_prepare: bool = False,
    storage_path: str = "",
    ablation: Optional[AblationParams] = None,
) -> AutoBenchmarkResult:
    """Run full autonomous benchmark pipeline.

    1. Select paper (explicit or top candidate)
    2. Prepare: fetch missing refs (unless skip_prepare)
    3. Analyst: extract ground truth
    4. Benchmark: baseline + reconstruction
    5. Persist run
    6. Compare with previous run

    Safety: backs up DB before mutations.
    """
    import subprocess
    import time

    from klemma import __version__

    from .candidates import discover_candidates
    from .prepare import prepare_benchmark
    from .reconstruction import run_reconstruction_benchmark
    from .runners import build_results_summary

    # 1. Select paper
    if not paper_citekey:
        candidates = discover_candidates(state, limit=1)
        if not candidates:
            return AutoBenchmarkResult(paper_citekey="", results={"error": "no candidates found"})
        paper_citekey = candidates[0].citekey

    result = AutoBenchmarkResult(paper_citekey=paper_citekey)

    # 2. Prepare: fetch missing referenced papers
    if not skip_prepare and storage_path:
        # Safety: backup DB before mutations
        db_path = state.db_path
        backup_path = db_path.with_suffix(".db.bak")
        shutil.copy2(db_path, backup_path)
        logger.info("DB backed up to %s", backup_path)

        result.prepare_result = prepare_benchmark(
            state, paper_citekey,
            storage_path=storage_path,
            dry_run=False,
        )

    # 3. Analyst: extract ground truth
    dataset = run_analyst_from_source(
        state, ai, paper_citekey, config, klemma_home,
    )
    if not dataset:
        result.results = {"error": f"analyst failed for {paper_citekey}"}
        return result

    # 4. Benchmark
    effective_ablation = ablation or AblationParams()
    t_start = time.monotonic()
    bench_results = run_reconstruction_benchmark(
        state, dataset, ai=ai, klemma_home=klemma_home,
        ablation=effective_ablation,
    )
    duration = time.monotonic() - t_start

    result.results = {"reconstruction": bench_results}

    # 5. Persist
    summary = build_results_summary(result.results)
    git_commit = ""
    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception:
        pass

    prompt_hash = compute_prompt_hash("reconstruct.md", klemma_home)

    run_id = state.save_benchmark_run(
        metrics_filter="reconstruct",
        ai_backend=config.ai.backend,
        ai_model=config.ai.model,
        results=result.results,
        results_summary=summary,
        paper_citekey=paper_citekey,
        duration_seconds=round(duration, 2),
        git_commit=git_commit,
        klemma_version=__version__,
        config_snapshot={
            "ai": {"backend": config.ai.backend, "model": config.ai.model},
            "auto": True,
            "ablation": effective_ablation.to_snapshot(),
            "prompt_hash": prompt_hash,
        },
    )
    result.run_id = run_id

    # 6. Compare with previous run
    runs = state.get_benchmark_runs(paper_citekey=paper_citekey, limit=2)
    if len(runs) >= 2:
        prev = runs[1]  # second most recent
        result.previous_run_id = prev["run_id"]
        result.comparison = state.compare_benchmark_runs(prev["run_id"], run_id)

    return result
