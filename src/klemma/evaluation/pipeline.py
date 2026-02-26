"""Full autonomous benchmark pipeline.

Composes: candidate selection → prepare (fetch missing refs) →
analyst (extract ground truth) → benchmark → persist → compare.
"""

from __future__ import annotations

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
    t_start = time.monotonic()
    bench_results = run_reconstruction_benchmark(
        state, dataset, ai=ai, klemma_home=klemma_home,
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
