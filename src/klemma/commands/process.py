"""Process and embed commands."""

import click
from rich.table import Table

from ..cli import (
    _get_context,
    _init_ai,
    _process_single,
    _resolve_emb,
    console,
    main,
)
from ..embeddings import SemanticScholarEmbeddings


@main.command()
@click.argument("citekeys", nargs=-1)
@click.option("--serial", is_flag=True, help="Disable parallel processing")
@click.option(
    "--force",
    is_flag=True,
    help="Reprocess completed sources, replacing existing fragments",
)
@click.option(
    "--model", default=None, help="Override AI model (e.g. openai/gpt-4.1-mini)"
)
@click.pass_context
def process(ctx, citekeys, serial, force, model):
    """Process source(s): extract fragments, annotate, create vault note.

    With CITEKEY(s): process specified sources (parallel when >1).
    Without arguments: process all pending sources.
    With --force: reprocess all completed sources, replacing their fragments.
    """
    kctx = _get_context(ctx)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    if model:
        cfg.ai.model = model
    ai = _init_ai(cfg)

    from ..literature.pdf import PDFExtractor

    pdf_extractor = PDFExtractor(max_chars=cfg.ai.max_pdf_chars)

    # Auto-resolve previously detected reference gaps against current library
    if kctx.library:
        resolved = state.resolve_gaps(kctx.library.entries)
        if resolved:
            console.print(f"[green]Auto-resolved {resolved} reference gap(s)[/green]")

    # Build citekey list: explicit, force-completed, or all pending
    if citekeys:
        keys = list(citekeys)
    elif force:
        keys = state.get_completed_sources()
        if not keys:
            console.print("[green]No completed sources to reprocess.[/green]")
            return
        console.print(
            f"[blue]Reprocessing {len(keys)} completed sources (replacing fragments)[/blue]"
        )
    else:
        proc_stats = state.get_stats()
        if proc_stats.get("pending", 0) == 0:
            console.print("[green]No pending sources to process.[/green]")
            return
        keys = state.get_pending_sources()
        console.print(f"[blue]Processing {len(keys)} pending sources[/blue]")

    parallel = len(keys) > 1 and not serial

    if parallel:
        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        t0 = time.monotonic()
        results = {}

        with console.status(
            f"Extracting fragments from {len(keys)} sources (3 workers)", spinner="arc"
        ):
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {
                    pool.submit(
                        _process_single,
                        ck,
                        cfg,
                        state,
                        vault,
                        ai,
                        pdf_extractor,
                        kctx.library,
                        quiet=True,
                        dissertation_context=kctx.dissertation_context,
                        available_tags=kctx.available_tags,
                        klemma_home=kctx.klemma_home,
                        embeddings=kctx.embeddings,
                        force=force,
                    ): ck
                    for ck in keys
                }
                for future in as_completed(futures):
                    ck = futures[future]
                    try:
                        results[ck] = future.result()
                    except Exception as e:
                        results[ck] = (0, f"error: {e}")

        elapsed = time.monotonic() - t0
        ok = 0
        newly_skipped = 0
        for idx, ck in enumerate(keys, 1):
            n_frags, status = results.get(ck, (0, "unknown"))
            if n_frags > 0:
                console.print(
                    f"  [{idx}/{len(keys)}] @{ck} \u2014 [green]{n_frags} fragments[/green]"
                )
                ok += 1
            else:
                console.print(f"  [{idx}/{len(keys)}] @{ck} \u2014 [red]{status}[/red]")
                if status in ("PDF not found", "text too short", "no fragments"):
                    newly_skipped += 1
        skip_msg = (
            f" {newly_skipped} skipped (no PDF / text too short)."
            if newly_skipped
            else ""
        )
        console.print(
            f"\n[green]Done: {ok}/{len(keys)} processed (parallel, {elapsed:.0f}s).[/green]{skip_msg}"
        )
    else:
        processed = 0
        newly_skipped = 0
        for idx, ck in enumerate(keys, 1):
            if len(keys) > 1:
                console.print(f"\n[bold][{idx}/{len(keys)}] {ck}[/bold]")
            n_frags, reason = _process_single(
                ck,
                cfg,
                state,
                vault,
                ai,
                pdf_extractor,
                kctx.library,
                dissertation_context=kctx.dissertation_context,
                available_tags=kctx.available_tags,
                klemma_home=kctx.klemma_home,
                project_type=kctx.project.type if kctx.project else "dissertation",
                embeddings=kctx.embeddings,
                force=force,
            )
            if n_frags > 0:
                processed += 1
            elif reason in ("PDF not found", "text too short", "no fragments"):
                newly_skipped += 1
        if len(keys) > 1:
            skip_msg = (
                f" {newly_skipped} skipped (no PDF / text too short)."
                if newly_skipped
                else ""
            )
            console.print(
                f"\n[green]Done: {processed}/{len(keys)} processed.[/green]{skip_msg}"
            )

    # DEV mode: show benchmark candidate hints
    if kctx.config.instance.dev_mode:
        from ..evaluation.candidates import discover_candidates, format_candidate_hint

        candidates = discover_candidates(kctx.state, limit=3)
        hint = format_candidate_hint(candidates)
        if hint:
            console.print(hint)


# --- Embed group ---


@main.group(invoke_without_command=True, name="embed")
@click.pass_context
def embed(ctx):
    """Compute and store embeddings.

    Subcommands:
      klemma embed sources    \u2014 embed sources (default)
      klemma embed fragments  \u2014 embed fragment text
      klemma embed sections   \u2014 compute section centroid embeddings
      klemma embed all        \u2014 run sources \u2192 fragments \u2192 sections in sequence

    Run `klemma embed sources --help` for source-embedding options.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(embed_sources)


main.add_command(embed)


@embed.command(name="sources")
@click.argument("citekeys", required=False, nargs=-1)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show how many would be embedded without calling API",
)
@click.option(
    "--backend",
    type=click.Choice(["s2", "local", "openai"]),
    help="Override embedding backend",
)
@click.option(
    "--backfill", is_flag=True, help="Fetch missing abstracts from S2 before embedding"
)
@click.pass_context
def embed_sources(ctx, citekeys, dry_run, backend, backfill):
    """Embed source title+abstract vectors.

    Without CITEKEYS: embed all sources missing embeddings.
    With CITEKEYS: embed specific sources by citekey.
    """
    kctx = _get_context(ctx)
    state = kctx.state
    emb = _resolve_emb(kctx, backend, dry_run)
    if emb is None:
        return

    # Get candidates
    if citekeys:
        candidates = []
        missing = []
        for ck in citekeys:
            source = state.get_source(ck)
            if not source:
                missing.append(ck)
                continue
            candidates.append(ck)
        if missing:
            console.print(f"[yellow]Missing citekeys: {', '.join(missing)}[/yellow]")
        if not candidates:
            return
    else:
        candidates = state.get_sources_without_embeddings()

    if not candidates:
        console.print("[green]All sources already have embeddings.[/green]")
        return

    is_s2 = isinstance(emb, SemanticScholarEmbeddings) if emb else False
    entries = kctx.library.entries if kctx.library else {}

    # --backfill: fetch missing abstracts from S2 before embedding
    if backfill and not is_s2:
        from ..literature.metadata import lookup_s2

        no_abs = [c for c in candidates if not (entries.get(c) and entries[c].abstract)]
        if no_abs:
            from rich.progress import Progress

            filled = 0
            with Progress(console=console) as progress:
                btask = progress.add_task(
                    "Backfilling abstracts from S2...", total=len(no_abs)
                )
                for ck in no_abs:
                    entry = entries.get(ck)
                    title = entry.title if entry else ck
                    if title and title != ck:
                        hit = lookup_s2(title)
                        if hit and hit.get("abstract"):
                            state.update_source_info(ck, abstract=hit["abstract"])
                            if entry:
                                entry.abstract = hit["abstract"]
                            filled += 1
                    progress.advance(btask)
            if filled:
                console.print(
                    f"[green]Backfilled {filled}/{len(no_abs)} abstracts.[/green]"
                )

    if dry_run:
        console.print(f"[blue]Would embed {len(candidates)} sources[/blue]")
        return

    embedded = 0
    api_miss = 0
    failed = 0

    from rich.progress import Progress

    with Progress(console=console) as progress:
        task = progress.add_task("Embedding sources...", total=len(candidates))
        for ck in candidates:
            entry = entries.get(ck)
            title = entry.title if entry else ck
            abstract = entry.abstract if entry else ""
            if not is_s2 and not abstract:
                frags = state.get_fragments(source_id=ck, limit=10)
                frag_text = " ".join(
                    f["fragment_text"] for f in frags if f.get("fragment_text")
                )
                abstract = frag_text[:2000] if frag_text else ""
            try:
                vec = emb.embed(title, abstract)
                if vec:
                    state.save_embedding(ck, vec, emb.model_name)
                    embedded += 1
                else:
                    api_miss += 1
            except Exception as e:
                console.print(f"  [red]{ck}: {e}[/red]")
                failed += 1
            progress.advance(task)

    emb_stats = state.get_embedding_stats()
    parts = [f"[green]Embedded: {embedded}[/green]"]
    if api_miss:
        parts.append(f"[yellow]Not found: {api_miss}[/yellow]")
    if failed:
        parts.append(f"[red]Failed: {failed}[/red]")
    parts.append(f"[dim]Total: {emb_stats['embedded']}/{emb_stats['total']}[/dim]")
    console.print("\n" + " | ".join(parts))


@embed.command(name="fragments")
@click.option("--dry-run", is_flag=True, help="Preview without API calls")
@click.option(
    "--backend",
    type=click.Choice(["s2", "local", "openai"]),
    help="Override embedding backend",
)
@click.pass_context
def embed_fragments(ctx, dry_run, backend):
    """Embed extracted fragment text vectors."""
    kctx = _get_context(ctx)
    state = kctx.state
    emb = _resolve_emb(kctx, backend, dry_run)
    if emb is None:
        return

    candidates = state.get_unembedded_fragments()
    if not candidates:
        console.print("[green]All fragments already have embeddings.[/green]")
        return
    if dry_run:
        console.print(f"[blue]Would embed {len(candidates)} fragments[/blue]")
        return

    embedded = 0
    failed = 0
    from rich.progress import Progress

    with Progress(console=console) as progress:
        task = progress.add_task("Embedding fragments...", total=len(candidates))
        for frag in candidates:
            try:
                vec = emb.embed(frag["fragment_text"])
                if vec:
                    state.save_fragment_embedding(frag["id"], vec, emb.model_name)
                    embedded += 1
                else:
                    failed += 1
            except Exception as e:
                console.print(f"  [red]Fragment {frag['id']}: {e}[/red]")
                failed += 1
            progress.advance(task)

    console.print(f"\n[green]Embedded: {embedded}[/green]", end="")
    if failed:
        console.print(f" | [red]Failed: {failed}[/red]", end="")
    console.print()


@embed.command(name="sections")
@click.option("--dry-run", is_flag=True, help="Preview without writing to DB")
@click.option(
    "--backend",
    type=click.Choice(["s2", "local", "openai"]),
    help="Override embedding backend",
)
@click.pass_context
def embed_sections(ctx, dry_run, backend):
    """Compute section centroid embeddings from source vectors."""
    kctx = _get_context(ctx)
    state = kctx.state
    emb = _resolve_emb(kctx, backend, dry_run)
    if emb is None:
        return

    model_name = emb.model_name if emb else None
    all_emb = state.get_all_embeddings(model=model_name)
    if not all_emb:
        console.print(
            "[yellow]No source embeddings found. Run `klemma embed sources` first.[/yellow]"
        )
        return

    with state._conn() as conn:
        cur = conn.execute(
            "SELECT DISTINCT section FROM source_sections ORDER BY section"
        )
        all_sections = [row["section"] for row in cur.fetchall()]

    embedded = 0
    skipped = 0
    for sec in all_sections:
        source_ids = state.get_section_sources(sec)
        vecs = [all_emb[sid] for sid in source_ids if sid in all_emb]
        if not vecs:
            skipped += 1
            continue
        dim = len(vecs[0])
        centroid = [sum(v[i] for v in vecs) / len(vecs) for i in range(dim)]
        if not dry_run:
            state.save_section_embedding(sec, centroid, model_name or "unknown", len(vecs))
        embedded += 1

    if dry_run:
        console.print(
            f"[blue]Would embed {embedded} sections ({skipped} have no source embeddings)[/blue]"
        )
    else:
        console.print(f"[green]Section embeddings: {embedded} computed[/green]", end="")
        if skipped:
            console.print(
                f" | [yellow]{skipped} skipped (no source embeddings)[/yellow]",
                end="",
            )
        console.print()


@embed.command(name="all")
@click.option("--dry-run", is_flag=True, help="Preview without API calls")
@click.option(
    "--backend",
    type=click.Choice(["s2", "local", "openai"]),
    help="Override embedding backend",
)
@click.pass_context
def embed_all(ctx, dry_run, backend):
    """Run sources \u2192 fragments \u2192 sections in sequence."""
    console.print("[dim]Step 1/3: sources[/dim]")
    ctx.invoke(embed_sources, dry_run=dry_run, backend=backend)
    console.print("\n[dim]Step 2/3: fragments[/dim]")
    ctx.invoke(embed_fragments, dry_run=dry_run, backend=backend)
    console.print("\n[dim]Step 3/3: sections[/dim]")
    ctx.invoke(embed_sections, dry_run=dry_run, backend=backend)


# --- Similar command ---


@main.command()
@click.argument("citekey_or_section")
@click.option("-k", "--top-k", default=10, help="Number of results (default: 10)")
@click.pass_context
def similar(ctx, citekey_or_section, top_k):
    """Find semantically similar sources.

    CITEKEY: find sources similar to a specific paper.
    SECTION (e.g. 2.3): find sources close to that section's centroid
    (useful for discovering cross-section recommendations).

    Requires embeddings to be stored (run `klemma embed` first).
    """
    from ..embeddings import cosine_similarity

    kctx = _get_context(ctx)
    state = kctx.state
    emb = kctx.embeddings

    if not emb:
        # Still try to use stored embeddings for comparison
        all_emb = state.get_all_embeddings()
        if not all_emb:
            console.print("[red]No embeddings found. Run `klemma embed` first.[/red]")
            return
        model_name = None
    else:
        model_name = emb.model_name
        all_emb = state.get_all_embeddings(model=model_name)
        if not all_emb:
            console.print("[red]No embeddings found. Run `klemma embed` first.[/red]")
            return

    # Determine if input is a citekey or section
    arg = citekey_or_section
    is_section = bool(arg[0].isdigit() and "." in arg)

    if is_section:
        # Section centroid mode
        section_sources = state.get_section_sources(arg)
        section_vecs = [all_emb[sid] for sid in section_sources if sid in all_emb]
        if not section_vecs:
            console.print(f"[red]No embedded sources for section {arg}[/red]")
            return
        dim = len(section_vecs[0])
        query_vec = [
            sum(v[i] for v in section_vecs) / len(section_vecs) for i in range(dim)
        ]
        console.print(
            f"[bold]Sources similar to section {arg}[/bold] "
            f"[dim](centroid of {len(section_vecs)} sources)[/dim]\n"
        )
        # Exclude sources already in this section
        exclude = set(section_sources)
    else:
        # Citekey mode
        result = state.get_embedding(arg)
        if not result:
            # Try to embed on the fly
            if emb and kctx.library:
                entry = kctx.library.entries.get(arg)
                if entry and entry.abstract:
                    vec = emb.embed(entry.title or arg, entry.abstract)
                    if vec:
                        state.save_embedding(arg, vec, emb.model_name)
                        query_vec = vec
                        console.print(f"[dim]Embedded @{arg} on the fly[/dim]\n")
                    else:
                        console.print(f"[red]Could not embed @{arg}[/red]")
                        return
                else:
                    console.print(
                        f"[red]No embedding for @{arg} and no abstract to embed[/red]"
                    )
                    return
            else:
                console.print(
                    f"[red]No embedding for @{arg}. Run `klemma embed {arg}` first.[/red]"
                )
                return
        else:
            query_vec = result[0]
        console.print(f"[bold]Sources similar to @{arg}[/bold]\n")
        exclude = {arg}

    # Compute similarities
    sims = []
    for sid, vec in all_emb.items():
        if sid in exclude:
            continue
        sim = cosine_similarity(query_vec, vec)
        sims.append((sid, sim))
    sims.sort(key=lambda x: x[1], reverse=True)

    # Display
    entries = kctx.library.entries if kctx.library else {}
    table = Table(show_edge=False, pad_edge=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("Source", style="cyan")
    table.add_column("Similarity", justify="right", width=8)
    table.add_column("Section", width=8)

    for i, (sid, sim) in enumerate(sims[:top_k], 1):
        entry = entries.get(sid)
        title = f"@{sid}"
        if entry:
            author_short = (entry.authors_str or "")[:25]
            year = entry.year or ""
            title = f"{author_short} ({year})"
        source = state.get_source(sid)
        sec = source.get("primary_section", "") if source else ""
        style = "green" if sim > 0.8 else "yellow" if sim > 0.5 else "dim"
        table.add_row(str(i), title, f"[{style}]{sim:.3f}[/{style}]", sec)

    console.print(table)

    if is_section:
        # Show cross-section discoveries
        cross = [
            (sid, sim)
            for sid, sim in sims[:top_k]
            if not any(sid in state.get_section_sources(arg))
        ]
        if cross:
            console.print(f"\n[dim]{len(cross)} sources from other sections[/dim]")
