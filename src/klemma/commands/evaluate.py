"""`klemma eval` — evaluation commands that are NOT unit tests.

`klemma eval extract` runs the exhaustive extraction engine N times over the
gold frame of each document and reports min/mean recall and per-run
precision against a hand-labelled frame (plan C4). Identity of the run
(model, prompt hash, outline hash, chunking, extractor version) is printed
into the report so a later re-evaluation is comparable.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..cli import _get_context, _init_ai, console, main


@main.group(name="eval")
def eval_group():
    """Evaluation runs (gold recall/precision) — separate from pytest."""


@eval_group.command(name="extract")
@click.option("--gold", "gold_dir", type=click.Path(exists=True, file_okay=False), required=True,
              help="Directory with <citekey>.json gold frames (outside git)")
@click.option("--mode", type=click.Choice(["exhaustive", "standard"]), default="exhaustive")
@click.option("--runs", type=int, default=3, show_default=True)
@click.option("--out", "out_path", type=click.Path(dir_okay=False), default=None,
              help="Write the Markdown report here (metrics and hashes only)")
@click.option("--manifest", "manifest_path", type=click.Path(dir_okay=False), default=None,
              help="Write a manifest (names + sha256) of the gold files for git")
@click.option("--candidates", "candidates_path", type=click.Path(dir_okay=False), default=None,
              help="Write text_key → fragment text of every scored fragment (outside git) to label precision")
@click.option("--recall-threshold", type=float, default=0.9, show_default=True)
@click.option("--precision-threshold", type=float, default=0.8, show_default=True)
@click.pass_context
def eval_extract(ctx, gold_dir, mode, runs, out_path, manifest_path, candidates_path,
                 recall_threshold, precision_threshold):
    """Recall/precision of the extraction engine on exhaustively annotated gold frames."""
    from ..config import resolve_prompt
    from ..evaluation.extract_eval import (
        GoldError,
        candidate_labels_template,
        evaluate,
        load_gold_dir,
        manifest,
        render_report,
        verdict,
    )
    from ..extraction_runs import EXTRACTOR_VERSION, canonical_config_json
    from ..hashing import compute_prompt_hash
    from ..literature.models import ZoteroEntry
    from ..literature.sidecar import load_sidecar_doc
    from ..skills.extract_engine import Budget, extract_from_pages
    from ..skills.outline_digest import outline_hash

    kctx = _get_context(ctx)
    cfg = kctx.config
    ai = _init_ai(cfg)
    try:
        gold = load_gold_dir(Path(gold_dir))
    except GoldError as exc:
        raise click.ClickException(str(exc))
    if not gold:
        console.print("[red]No gold files found[/red]")
        raise SystemExit(1)

    prompt_name = "extract_exhaustive.md" if mode == "exhaustive" else "extract.md"
    prompt_path = resolve_prompt(prompt_name, kctx.klemma_home)
    prompt_vars = {
        "dissertation_context": kctx.dissertation_context,
        "available_tags": ", ".join(kctx.available_tags) if kctx.available_tags else "",
        "language": cfg.ai.language,
        "project_type": kctx.project.type if kctx.project else "dissertation",
        "outline_digest": kctx.outline_digest,
    }

    def _frame_pages(doc):
        sc = load_sidecar_doc(kctx.project_root, doc.citekey)
        if sc is None:
            raise click.ClickException(f"{doc.citekey}: no sidecar — run `klemma repair --steps sidecar`")
        lo, hi = doc.frame_pages
        pages_by_no: dict[int, str] = {}
        for page, start, end in sc.page_spans:
            pages_by_no[page] = pages_by_no.get(page, "") + sc.text[start:end]
        return [pages_by_no.get(n, "") for n in range(lo, hi + 1)]

    def _runner(doc, run_index):
        pages = _frame_pages(doc)
        entry = ZoteroEntry(id=doc.citekey, title=doc.citekey)
        outcome = extract_from_pages(
            pages, entry, prompt_path, prompt_vars, ai,
            chunk_size=cfg.ai.chunk_size, overlap=cfg.ai.chunk_overlap,
            min_chunk_chars=cfg.ai.min_chunk_chars,
            max_tokens_cap=cfg.ai.exhaustive_max_tokens if mode == "exhaustive" else cfg.ai.max_tokens_cap,
            mode=mode, budget=Budget(), pricing=cfg.ai.pricing or None,
        )
        if outcome.error:
            raise click.ClickException(f"{doc.citekey} run {run_index}: {outcome.error}")
        console.print(
            f"  {doc.citekey} run {run_index + 1}/{runs}: {len(outcome.fragments)} fragments, "
            f"coverage {outcome.coverage.ratio * 100:.0f}%, failed {outcome.failed_chunks}"
        )
        # Same page-marked text the engine chunked (renumbered from 1 inside the
        # frame; gold quotes straddling a marker are matched marker-free).
        from ..skills.extract_engine import build_full_text

        frame_text = build_full_text(pages)
        frags = [
            (ef.fragment.text, (ef.char_start, ef.char_end) if ef.char_start is not None else None)
            for ef in outcome.fragments
        ]
        return frags, frame_text

    results = evaluate(gold, _runner, runs=runs)
    identity = {
        "mode": mode,
        "model": cfg.ai.model,
        "temperature": "provider default (Claude 5 rejects temperature != 1)",
        "template_hash": compute_prompt_hash(Path(prompt_path).read_text(encoding="utf-8")),
        "outline_hash": outline_hash(kctx.outline_digest) if kctx.outline_digest else "",
        "config": canonical_config_json(cfg.ai),
        "extractor_version": EXTRACTOR_VERSION,
        "runs": runs,
    }
    report = render_report(results, identity=identity, recall_threshold=recall_threshold,
                           precision_threshold=precision_threshold)
    console.print(report)
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(report, encoding="utf-8")
        console.print(f"[green]Report written:[/green] {out_path}")
    if manifest_path:
        Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(manifest_path).write_text(
            json.dumps(manifest(Path(gold_dir)), ensure_ascii=False, indent=2), encoding="utf-8",
        )
        console.print(f"[green]Manifest written:[/green] {manifest_path}")
    if candidates_path:
        Path(candidates_path).parent.mkdir(parents=True, exist_ok=True)
        Path(candidates_path).write_text(
            json.dumps(candidate_labels_template(results), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        console.print(f"[green]Candidates for labelling written:[/green] {candidates_path}")
    v = verdict(results, recall_threshold=recall_threshold, precision_threshold=precision_threshold)
    if not v.passed:
        raise SystemExit(1)
