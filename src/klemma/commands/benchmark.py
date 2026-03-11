"""Benchmark command — evaluation framework."""

import click

from ..cli import (
    _get_context,
    _init_ai,
    _print_benchmark_compare,
    _print_benchmark_history,
    _print_reconstruction_results,
    _run_analyst_mode,
    _run_auto_mode,
    _run_prepare_mode,
    _sync_sections,
    console,
    main,
)


@main.command()
@click.option(
    "--dataset",
    "-d",
    type=click.Path(exists=True),
    help="Path to annotated benchmark dataset JSON",
)
@click.option(
    "--metrics",
    "-m",
    type=click.Choice(["all", "intent", "gaps", "embeddings", "reconstruct"]),
    default="all",
    help="Which benchmarks to run (default: all)",
)
@click.option(
    "--export",
    "export_path",
    type=click.Path(),
    help="Export current DB data as dataset template for annotation",
)
@click.option(
    "--json-output", is_flag=True, help="Output results as JSON for reproducibility"
)
@click.option(
    "--semantic",
    is_flag=True,
    help="Apply semantic reranking to gap benchmark (hybrid keyword × semantic mode)",
)
@click.option(
    "--analyst",
    "analyst_citekey",
    type=str,
    default=None,
    help="Run analyst prompt on a paper PDF to extract ground truth citation map",
)
@click.option(
    "--reconstruct",
    "reconstruct",
    is_flag=True,
    help="Run citation reconstruction benchmark (requires reconstruction field in dataset)",
)
@click.option("--history", is_flag=True, help="Show past benchmark run history")
@click.option(
    "--compare",
    nargs=2,
    type=str,
    default=None,
    help="Compare two runs: --compare <id1> <id2>",
)
@click.option(
    "--export-history",
    "export_history_path",
    type=click.Path(),
    help="Export benchmark run history as JSON for archival",
)
@click.option(
    "--candidates",
    is_flag=True,
    help="Show benchmark candidate papers ranked by citation graph coverage",
)
@click.option(
    "-k",
    "candidates_limit",
    type=int,
    default=10,
    help="Number of candidates to show (default: 10)",
)
@click.option(
    "--prepare",
    "prepare_citekey",
    type=str,
    default=None,
    help="Fetch missing referenced papers for a citekey (dry-run first)",
)
@click.option(
    "--auto",
    "auto_mode",
    is_flag=True,
    help="Run full autonomous pipeline: select → prepare → analyst → benchmark → persist",
)
@click.option(
    "--paper",
    "auto_paper",
    type=str,
    default=None,
    help="Citekey for --auto mode (default: top candidate)",
)
@click.option(
    "--skip-prepare", is_flag=True, help="Skip reference preparation in --auto mode"
)
@click.option(
    "--temperature",
    "ablation_temperature",
    type=float,
    default=None,
    help="Override AI temperature for ablation (default: 0.2)",
)
@click.option(
    "--max-recs",
    "ablation_max_recs",
    type=int,
    default=None,
    help="Max recommendations per section (default: uncapped)",
)
@click.option(
    "--fragments",
    "ablation_fragments",
    type=int,
    default=None,
    help="Fragments per source for context (default: 5)",
)
@click.option(
    "--prompt-variant",
    "ablation_variant",
    type=click.Choice(["default", "fewshot"]),
    default=None,
    help="Prompt variant for ablation (default: default)",
)
@click.pass_context
def benchmark(
    ctx,
    dataset,
    metrics,
    export_path,
    json_output,
    semantic,
    analyst_citekey,
    reconstruct,
    history,
    compare,
    export_history_path,
    candidates,
    candidates_limit,
    prepare_citekey,
    auto_mode,
    auto_paper,
    skip_prepare,
    ablation_temperature,
    ablation_max_recs,
    ablation_fragments,
    ablation_variant,
):
    """Run evaluation benchmarks against annotated ground truth.

    Multi-format evaluation (Singh et al. 2023 — SciRepEval):
    intent classification, gap ranking, embedding retrieval,
    and citation reconstruction evaluated separately.

    Use --export to generate a dataset template from current DB,
    then manually review/correct labels to create ground truth.

    Use --semantic to measure hybrid gap ranking (keyword score × semantic
    similarity), requires embeddings to be configured.

    Use --analyst <citekey> to extract ground truth from a paper's PDF.

    Use --reconstruct to run citation reconstruction benchmark.

    Use --history to show past benchmark run history.

    Use --compare <id1> <id2> to compare two runs side-by-side.

    Use --export-history <path> to export run history as JSON for archival.
    """
    import json
    import subprocess
    import time
    from pathlib import Path

    from rich.panel import Panel
    from rich.table import Table

    from .. import __version__
    from ..evaluation import build_results_summary, load_dataset, run_all
    from ..evaluation.dataset import export_dataset
    from ..repositories.benchmarks import compute_dataset_hash

    kctx = _get_context(ctx)
    _sync_sections(kctx, quiet=True)

    # --- History mode ---
    if history:
        _print_benchmark_history(kctx.state)
        return

    # --- Compare mode ---
    if compare:
        _print_benchmark_compare(kctx.state, compare[0], compare[1])
        return

    # --- Export history ---
    if export_history_path:
        runs = kctx.state.get_benchmark_runs(limit=1000)
        with open(export_history_path, "w") as f:
            json.dump(runs, f, indent=2, default=str)
        console.print(
            f"[green]Exported {len(runs)} runs to {export_history_path}[/green]"
        )
        return

    # --- Candidates mode ---
    if candidates:
        from ..evaluation.candidates import discover_candidates

        cands = discover_candidates(kctx.state, limit=candidates_limit)
        if not cands:
            console.print(
                "[yellow]No benchmark candidates found (need sources with ≥3 in-library citations)[/yellow]"
            )
            return
        t = Table(title="Benchmark Candidates")
        t.add_column("Citekey")
        t.add_column("In-lib", justify="right")
        t.add_column("Total", justify="right")
        t.add_column("Intents", justify="right")
        t.add_column("PDF")
        t.add_column("Benchmarked")
        t.add_column("Score", justify="right")
        for c in cands:
            t.add_row(
                c.citekey,
                str(c.in_library_citations),
                str(c.total_citations),
                str(c.intent_diversity),
                "[green]yes[/green]" if c.has_pdf else "[red]no[/red]",
                "[dim]yes[/dim]" if c.already_benchmarked else "no",
                f"{c.score:.0f}",
            )
        console.print(t)
        return

    # --- Prepare mode: fetch missing referenced papers ---
    if prepare_citekey:
        _run_prepare_mode(kctx, prepare_citekey)
        return

    # --- Auto mode: full autonomous pipeline ---
    if auto_mode:
        from ..evaluation.pipeline import AblationParams

        ablation = None
        if any(
            v is not None
            for v in [
                ablation_temperature,
                ablation_max_recs,
                ablation_fragments,
                ablation_variant,
            ]
        ):
            kwargs = {}
            if ablation_temperature is not None:
                kwargs["temperature"] = ablation_temperature
            if ablation_max_recs is not None:
                kwargs["max_recs_per_section"] = ablation_max_recs
            if ablation_fragments is not None:
                kwargs["fragments_per_source"] = ablation_fragments
            if ablation_variant == "fewshot":
                ablation = AblationParams.with_fewshot(**kwargs)
            else:
                ablation = AblationParams(**kwargs)

        _run_auto_mode(kctx, auto_paper, skip_prepare, ablation=ablation)
        return

    # --- Analyst mode: extract ground truth from a paper ---
    if analyst_citekey:
        _run_analyst_mode(kctx, analyst_citekey, json_output)
        return

    if export_path:
        count = export_dataset(kctx.state, Path(export_path))
        console.print(f"[green]Exported {count} items to {export_path}[/green]")
        console.print(
            "Review and correct ground_truth labels, then run: "
            f"klemma benchmark -d {export_path}"
        )
        return

    if not dataset:
        console.print(
            "[yellow]No dataset specified. Use --dataset/-d to provide "
            "annotated ground truth, or --export to generate a template.[/yellow]"
        )
        return

    t_start = time.monotonic()
    ds = load_dataset(Path(dataset))
    recon_info = (
        f", reconstruction: {len(ds.reconstruction.samples)} samples"
        if ds.reconstruction
        else ""
    )
    console.print(
        f"Dataset: {len(ds.fragments)} fragments, "
        f"{len(ds.gaps)} gaps, {len(ds.similar_pairs)} similarity pairs"
        f"{recon_info}"
    )

    reranked_gaps = None
    if semantic and kctx.embeddings:
        _bsw = kctx.project.section_weights if kctx.project else None
        all_gaps = kctx.state.get_reference_gaps(limit=100, section_weights=_bsw)
        reranked_gaps = kctx.state.rerank_gaps_semantic(all_gaps, kctx.embeddings)
    elif semantic:
        console.print(
            "[yellow]--semantic requires embeddings to be configured[/yellow]"
        )

    # Determine effective metrics filter
    effective_metrics = "reconstruct" if reconstruct else metrics

    # Build ablation params for -d mode (same logic as --auto)
    from ..evaluation.pipeline import AblationParams, compute_prompt_hash

    ablation = None
    if any(
        v is not None
        for v in [
            ablation_temperature,
            ablation_max_recs,
            ablation_fragments,
            ablation_variant,
        ]
    ):
        kwargs = {}
        if ablation_temperature is not None:
            kwargs["temperature"] = ablation_temperature
        if ablation_max_recs is not None:
            kwargs["max_recs_per_section"] = ablation_max_recs
        if ablation_fragments is not None:
            kwargs["fragments_per_source"] = ablation_fragments
        if ablation_variant == "fewshot":
            ablation = AblationParams.with_fewshot(**kwargs)
        else:
            ablation = AblationParams(**kwargs)

    if ablation:
        params = ablation.to_snapshot()
        non_default = {
            k: v for k, v in params.items() if v is not None and k != "prompt_variant"
        }
        if non_default or params.get("prompt_variant") != "default":
            console.print(f"[dim]Ablation: {params}[/dim]")

    # Initialize AI if reconstruction benchmark is requested
    ai = None
    if (effective_metrics in ("all", "reconstruct")) and ds.reconstruction:
        try:
            ai = _init_ai(kctx.config)
        except Exception:
            console.print(
                "[dim]AI not available — reconstruction will run baseline only[/dim]"
            )

    results = run_all(
        kctx.state,
        ds,
        effective_metrics,
        reranked_gaps=reranked_gaps,
        ai=ai,
        klemma_home=kctx.klemma_home,
        ablation=ablation,
    )

    duration = time.monotonic() - t_start

    # --- Persist run ---
    ds_hash = compute_dataset_hash(dataset)
    git_commit = ""
    try:
        git_commit = (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            .decode()
            .strip()
        )
    except Exception:
        pass

    paper_citekey = ""
    if ds.reconstruction and ds.reconstruction.ground_truth:
        paper_citekey = ds.reconstruction.ground_truth.paper_citekey

    summary = build_results_summary(results)
    prompt_hash = compute_prompt_hash("reconstruct.md", kctx.klemma_home)
    effective_ablation = ablation or AblationParams()
    run_id = kctx.state.save_benchmark_run(
        dataset_path=dataset,
        dataset_hash=ds_hash,
        metrics_filter=effective_metrics,
        ai_backend=kctx.config.ai.backend,
        ai_model=kctx.config.ai.model,
        results=results,
        results_summary=summary,
        paper_citekey=paper_citekey,
        duration_seconds=round(duration, 2),
        git_commit=git_commit,
        klemma_version=__version__,
        config_snapshot={
            "ai": {"backend": kctx.config.ai.backend, "model": kctx.config.ai.model},
            "frozen_gt": True,
            "ablation": effective_ablation.to_snapshot(),
            "prompt_hash": prompt_hash,
        },
    )
    console.print(f"[dim]Run {run_id} saved ({duration:.1f}s)[/dim]")

    if json_output:
        click.echo(json.dumps(results, indent=2))
        return

    # Rich table output
    if "intent" in results:
        ir = results["intent"]
        m = ir.get("metrics", {})
        console.print(
            Panel(
                f"Matched: {ir['matched']}/{ir['total']} "
                f"(skipped: {ir.get('skipped', 0)})\n"
                f"[bold]Macro-F1: {m.get('macro_f1', 0):.4f}[/bold]  "
                f"Accuracy: {m.get('accuracy', 0):.4f}",
                title="Intent Classification",
            )
        )
        if m.get("per_class"):
            t = Table(title="Per-class metrics")
            t.add_column("Intent")
            t.add_column("Precision", justify="right")
            t.add_column("Recall", justify="right")
            t.add_column("F1", justify="right")
            t.add_column("Support", justify="right")
            for cls, vals in m["per_class"].items():
                t.add_row(
                    cls,
                    f"{vals['precision']:.4f}",
                    f"{vals['recall']:.4f}",
                    f"{vals['f1']:.4f}",
                    str(vals["support"]),
                )
            console.print(t)

    if "gaps" in results:
        gr = results["gaps"]
        gm = gr.get("metrics", {})
        gap_title = (
            "Gap Ranking [dim](hybrid: keyword × semantic)[/dim]"
            if semantic
            else "Gap Ranking"
        )
        console.print(
            Panel(
                f"Ground truth: {gr['total']} gaps, "
                f"DB gaps: {gr.get('db_gaps_count', 0)}\n"
                f"Precision@5: {gm.get('precision_at_5', 0):.4f}  "
                f"Precision@10: {gm.get('precision_at_10', 0):.4f}  "
                f"[bold]nDCG@10: {gm.get('ndcg_at_10', 0):.4f}[/bold]",
                title=gap_title,
            )
        )

    if "embeddings" in results:
        er = results["embeddings"]
        em = er.get("metrics", {})
        if er.get("error"):
            console.print(f"[yellow]Embeddings: {er['error']}[/yellow]")
        else:
            console.print(
                Panel(
                    f"Queries: {er.get('evaluated', 0)}/{er['total_queries']} "
                    f"(skipped: {er.get('skipped', 0)})\n"
                    f"Recall@5: {em.get('avg_recall_at_5', 0):.4f}  "
                    f"[bold]Recall@10: {em.get('avg_recall_at_10', 0):.4f}[/bold]  "
                    f"Precision@5: {em.get('avg_precision_at_5', 0):.4f}",
                    title="Embedding Retrieval",
                )
            )

    if "reconstruction" in results:
        _print_reconstruction_results(results["reconstruction"])
