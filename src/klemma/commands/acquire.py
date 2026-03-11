"""Acquire and add commands."""

import click

from ..cli import (
    _get_context,
    _init_ai,
    _process_single,
    console,
    main,
)


@main.command()
@click.argument("url", required=False)
@click.option("--title", "-t", help="Paper title")
@click.option("--authors", "-a", help="Authors (comma-separated)")
@click.option("--year", "-y", type=int, help="Publication year")
@click.option("--journal", "-j", help="Journal name")
@click.option("--volume", help="Volume")
@click.option("--issue", help="Issue")
@click.option(
    "--section", "-s", multiple=True, help="Dissertation section(s) to assign"
)
@click.option(
    "--pdf",
    "pdf_url",
    help="Direct PDF URL (bypass DOI resolution, e.g. for WAF-protected publishers)",
)
@click.option(
    "--batch",
    "batch_path",
    type=click.Path(exists=True),
    help="JSON file with papers list",
)
@click.option(
    "--no-process", is_flag=True, help="Skip fragment extraction after adding"
)
@click.pass_context
def acquire(
    ctx,
    url,
    title,
    authors,
    year,
    journal,
    volume,
    issue,
    section,
    pdf_url,
    batch_path,
    no_process,
):
    """Download PDF, add to Zotero, register in klemma.

    Single paper: klemma acquire <pdf_url> --title "..." --authors "..." --year 2022 --section 1.2
    With DOI + direct PDF: klemma acquire <doi_url> --pdf <direct_pdf_url> --section 1.3
    Batch: klemma acquire --batch papers.json
    """
    from ..skills.acquirer import PaperMetadata, acquire_paper_local, load_batch

    kctx = _get_context(ctx)
    cfg, state = kctx.config, kctx.state

    # Build paper list
    if batch_path:
        papers = load_batch(batch_path)
        console.print(f"[blue]Loaded {len(papers)} papers from batch file[/blue]")
    elif url:
        papers = [
            PaperMetadata(
                url=url,
                title=title or "",
                authors=authors or "",
                year=year,
                journal=journal or "",
                volume=volume or "",
                issue=issue or "",
                pdf_override=pdf_url or "",
                sections=list(section),
            )
        ]
    else:
        console.print("[red]Provide a URL or --batch file[/red]")
        return

    ok = 0

    for i, meta in enumerate(papers, 1):
        label = meta.title[:50] if meta.title else meta.url[:50]
        console.print(f"\n[bold][{i}/{len(papers)}] {label}[/bold]")

        result = acquire_paper_local(
            meta,
            storage_path=cfg.zotero.storage_path,
            state=state,
        )

        if result.status in ("ok", "ok_no_pdf"):
            console.print(f"  [green]@{result.citekey}[/green]")
            if meta.title:
                auto = " [dim](auto-extracted)[/dim]" if not title else ""
                console.print(f"  Title: {meta.title}{auto}")
            if meta.authors:
                console.print(f"  Authors: {meta.authors}")
            if meta.year:
                console.print(f"  Year: {meta.year}")
            if result.zotero_added:
                console.print("  [blue]Added to Zotero (BBT citekey)[/blue]")
            if result.status == "ok_no_pdf":
                console.print(
                    "  [yellow]No open-access PDF found (metadata-only)[/yellow]"
                )
                if result.zotero_added:
                    console.print(
                        "  [dim]Tip: use Zotero → Right-click → Find Available PDF[/dim]"
                    )
            if meta.sections:
                console.print(f"  [dim]sections: {', '.join(meta.sections)}[/dim]")

            if not no_process and result.status == "ok":
                try:
                    ai = _init_ai(cfg)
                except Exception as e:
                    console.print(
                        f"  [yellow]Skipping auto-process (AI unavailable: {e})[/yellow]"
                    )
                    console.print(
                        f"  [dim]Run manually: klemma process {result.citekey}[/dim]"
                    )
                    ai = None

                if ai:
                    from ..literature.pdf import PDFExtractor

                    pdf_extractor = PDFExtractor(max_chars=cfg.ai.max_pdf_chars)
                    with console.status(
                        f"Extracting fragments from @{result.citekey}", spinner="arc"
                    ):
                        _process_single(
                            result.citekey,
                            cfg,
                            state,
                            kctx.vault,
                            ai,
                            pdf_extractor,
                            kctx.library,
                            dissertation_context=kctx.dissertation_context,
                            available_tags=kctx.available_tags,
                            klemma_home=kctx.klemma_home,
                            project_type=(
                                kctx.project.type if kctx.project else "dissertation"
                            ),
                        )

            ok += 1
        else:
            console.print(f"  [red]{result.status}[/red]")

    console.print(f"\n[green]Done: {ok}/{len(papers)} acquired.[/green]")

    # DEV mode: show benchmark candidate hints
    if kctx.config.instance.dev_mode:
        from ..evaluation.candidates import discover_candidates, format_candidate_hint

        candidates = discover_candidates(kctx.state, limit=3)
        hint = format_candidate_hint(candidates)
        if hint:
            console.print(hint)
