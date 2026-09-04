"""Process and embed commands."""

import logging

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

logger = logging.getLogger(__name__)


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
@click.option(
    "--no-embed", is_flag=True, help="Skip auto-embedding after processing"
)
@click.option(
    "--replace", is_flag=True,
    help="With --force: drop the legacy (run-less) fragments once the new run is complete",
)
@click.option(
    "--exhaustive", is_flag=True, hidden=True,
    help="Best-effort exhaustive extraction (arrives with the exhaustive prompt; not yet available)",
)
@click.option(
    "--from-file", "from_file", type=click.Path(exists=True, dir_okay=False),
    help="Read citekeys from a file (one per line, # comments)",
)
@click.option("--resume-stale", is_flag=True, help="Mark runs stuck in 'running' > 2h as failed and re-run them")
@click.option("--activate-partial", "activate_run", type=int, default=None,
              help="Explicitly activate a pending partial run by id (requires --reason)")
@click.option("--reason", default="", help="Reason recorded with --activate-partial")
@click.pass_context
def process(ctx, citekeys, serial, force, model, no_embed, replace, exhaustive, from_file,
            resume_stale, activate_run, reason):
    """Process source(s): extract fragments, annotate, create vault note.

    With CITEKEY(s): process specified sources (parallel when >1).
    Without arguments: process all pending sources.
    With --force: re-extract completed sources; old fragments are kept and the
    new run becomes the active set only when complete (plan C2). Add
    --replace to drop the legacy corpus after a complete run.
    """
    kctx = _get_context(ctx)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    project_store = kctx.project_store

    if activate_run is not None:
        if project_store is None:
            console.print("[red]No project store — cannot activate runs[/red]")
            raise SystemExit(1)
        try:
            project_store.activate_partial(activate_run, reason)
        except (ValueError, RuntimeError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise SystemExit(1)
        run = project_store.get_run(activate_run)
        console.print(
            f"[green]Run #{activate_run} for @{run['citekey']} activated as published_partial[/green] "
            f"[dim]({reason.strip()})[/dim]"
        )
        return

    stale_keys: list[str] = []
    if project_store is not None:
        try:
            stale_rows = project_store.mark_stale_runs_detailed(2.0)
            if resume_stale:
                stale_keys = list(dict.fromkeys(r["citekey"] for r in stale_rows))
            if stale_rows:
                console.print(f"[yellow]{len(stale_rows)} stale run(s) marked failed (error=stale)[/yellow]")
                # Close the matching library attempts so both stores agree.
                if kctx.paper_store is not None:
                    for r in stale_rows:
                        if r.get("attempt_id"):
                            try:
                                kctx.paper_store.finish_attempt(r["attempt_id"], status="failed")
                            except Exception as exc:  # noqa: BLE001
                                logger.debug("finish stale attempt %s: %s", r["attempt_id"], exc)
        except Exception as exc:  # noqa: BLE001
            logger.debug("stale-run cleanup failed: %s", exc)

    if exhaustive:
        # The engine only gates finish_reason in this mode; the exhaustive
        # prompt/cap policy is not implemented yet. Recording mode='exhaustive'
        # for a standard extraction would poison provenance and evals.
        console.print("[red]--exhaustive is not available yet (plan C4).[/red]")
        raise SystemExit(2)
    if replace and not force:
        console.print("[red]--replace requires --force (it drops the legacy corpus after a "
                      "complete, validated run).[/red]")
        raise SystemExit(2)
    mode = "standard"

    from ..literature.pdf import PDFExtractor

    pdf_extractor = PDFExtractor(max_chars=cfg.ai.max_pdf_chars)

    # Auto-resolve previously detected reference gaps against current library
    if kctx.library:
        resolved = state.resolve_gaps(kctx.library.entries)
        if resolved:
            console.print(f"[green]Auto-resolved {resolved} reference gap(s)[/green]")

    # Build citekey list: explicit, --from-file, stale, force-completed, or all pending
    if from_file:
        with open(from_file, encoding="utf-8") as fh:
            file_keys = [
                ln.strip().lstrip("@") for ln in fh
                if ln.strip() and not ln.strip().startswith("#")
            ]
        citekeys = tuple(citekeys) + tuple(file_keys)
    if resume_stale:
        if not stale_keys:
            console.print("[green]No stale runs to resume.[/green]")
            return
        citekeys = tuple(citekeys) + tuple(k for k in stale_keys if k not in citekeys)
        force = True
    if model:
        cfg.ai.model = model
    ai = _init_ai(cfg)
    if citekeys:
        keys = list(dict.fromkeys(citekeys))
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
                        no_embed=no_embed,
                        paper_store=kctx.paper_store,
                        user_library=kctx.user_library,
                        project_store=project_store,
                        replace=replace,
                        mode=mode,
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
                no_embed=no_embed,
                paper_store=kctx.paper_store,
                user_library=kctx.user_library,
                project_store=project_store,
                replace=replace,
                mode=mode,
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
    type=click.Choice(["s2", "local", "openai", "litellm"]),
    help="Override embedding backend",
)
@click.option(
    "--backfill", is_flag=True, help="Fetch missing abstracts from S2 before embedding"
)
@click.option(
    "--remodel",
    is_flag=True,
    help="Also re-embed rows whose embedding_model differs from the current backend",
)
@click.pass_context
def embed_sources(ctx, citekeys, dry_run, backend, backfill, remodel):
    """Embed source title+abstract vectors.

    Without CITEKEYS: embed all sources missing embeddings.
    With CITEKEYS: embed specific sources by citekey.
    With --remodel: also re-embed sources whose stored embedding came
    from a different model (used when switching providers).
    """
    kctx = _get_context(ctx)
    state = kctx.state
    emb = _resolve_emb(kctx, backend, dry_run)
    if emb is None:
        return

    paper_store = kctx.paper_store
    user_library = kctx.user_library

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
        if remodel:
            stale = state.get_sources_with_stale_model(emb.model_name)
            if stale:
                console.print(
                    f"[dim]--remodel: {len(stale)} source(s) with stale "
                    f"embedding_model will be re-embedded[/dim]"
                )
                seen = set(candidates)
                candidates = list(candidates) + [ck for ck in stale if ck not in seen]

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
    lib_hits = 0

    from rich.progress import Progress

    with Progress(console=console) as progress:
        task = progress.add_task("Embedding sources...", total=len(candidates))
        for ck in candidates:
            # Resolve paper_id once per source for library dedup
            _paper_id = None
            if paper_store and user_library:
                try:
                    _paper_id = user_library.resolve_paper_id(ck)
                except Exception as e:
                    logger.debug("Library paper_id lookup failed for %s: %s", ck, e)

            # Library cache check: skip API if embedding already in library.db
            if _paper_id:
                try:
                    cached_vec = paper_store.get_paper_embedding(_paper_id, emb.model_name)
                    if cached_vec:
                        state.save_embedding(ck, cached_vec, emb.model_name)
                        embedded += 1
                        lib_hits += 1
                        progress.advance(task)
                        continue
                except Exception as e:
                    logger.debug("Library embedding cache check failed for %s: %s", ck, e)

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
                    # Write-through to library.db
                    if _paper_id:
                        try:
                            paper_store.save_paper_embedding(_paper_id, vec, emb.model_name)
                        except Exception as e:
                            logger.debug(
                                "Library embedding write-through failed for %s: %s", ck, e
                            )
                else:
                    api_miss += 1
            except Exception as e:
                console.print(f"  [red]{ck}: {e}[/red]")
                failed += 1
            progress.advance(task)

    emb_stats = state.get_embedding_stats()
    parts = [f"[green]Embedded: {embedded}[/green]"]
    if lib_hits:
        parts.append(f"[dim]Library cache: {lib_hits}[/dim]")
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
    type=click.Choice(["s2", "local", "openai", "litellm"]),
    help="Override embedding backend",
)
@click.option(
    "--remodel",
    is_flag=True,
    help="Also re-embed fragments whose embedding_model differs from the current backend",
)
@click.pass_context
def embed_fragments(ctx, dry_run, backend, remodel):
    """Embed extracted fragment text vectors.

    With --remodel: also re-embed fragments whose stored embedding came
    from a different model (used when switching providers).
    """
    kctx = _get_context(ctx)
    state = kctx.state
    paper_store = kctx.paper_store
    user_library = kctx.user_library
    emb = _resolve_emb(kctx, backend, dry_run)
    if emb is None:
        return

    candidates = state.get_unembedded_fragments()
    if remodel:
        stale = state.get_fragments_with_stale_model(emb.model_name)
        if stale:
            console.print(
                f"[dim]--remodel: {len(stale)} fragment(s) with stale "
                f"embedding_model will be re-embedded[/dim]"
            )
            seen_ids = {f["id"] for f in candidates}
            candidates = list(candidates) + [f for f in stale if f["id"] not in seen_ids]
    if not candidates:
        console.print("[green]All fragments already have embeddings.[/green]")
        return
    if dry_run:
        console.print(f"[blue]Would embed {len(candidates)} fragments[/blue]")
        return

    embedded = 0
    failed = 0
    lib_hits = 0

    from rich.progress import Progress

    from ..hashing import compute_content_hash

    # Per-paper_id cache: {paper_id: {content_hash: vector}}
    _lib_cache: dict[str, dict[str, list[float]]] = {}

    def _get_lib_frag_cache(paper_id: str) -> dict[str, list[float]]:
        if paper_id not in _lib_cache:
            try:
                _lib_cache[paper_id] = paper_store.get_fragment_embeddings(
                    paper_id, emb.model_name
                )
            except Exception as e:
                logger.debug(
                    "Library fragment-embedding cache failed for %s: %s", paper_id, e
                )
                _lib_cache[paper_id] = {}
        return _lib_cache[paper_id]

    with Progress(console=console) as progress:
        task = progress.add_task("Embedding fragments...", total=len(candidates))
        for frag in candidates:
            lib_hit = False

            # Library cache check
            if paper_store and user_library:
                try:
                    paper_id = user_library.resolve_paper_id(frag["citekey"])
                    if paper_id:
                        cache = _get_lib_frag_cache(paper_id)
                        ch = compute_content_hash(
                            paper_id, frag["fragment_text"], frag.get("page_number")
                        )
                        if ch in cache:
                            state.save_fragment_embedding(
                                frag["id"], cache[ch], emb.model_name
                            )
                            embedded += 1
                            lib_hits += 1
                            lib_hit = True
                except Exception as e:
                    logger.debug(
                        "Library cache check failed for fragment %s: %s",
                        frag["id"], e,
                    )

            if not lib_hit:
                try:
                    vec = emb.embed(frag["fragment_text"])
                    if vec:
                        state.save_fragment_embedding(frag["id"], vec, emb.model_name)
                        embedded += 1
                        # Write-through to library.db
                        if paper_store and user_library:
                            try:
                                paper_id = user_library.resolve_paper_id(frag["citekey"])
                                if paper_id:
                                    ch = compute_content_hash(
                                        paper_id,
                                        frag["fragment_text"],
                                        frag.get("page_number"),
                                    )
                                    paper_store.save_fragment_embedding(
                                        ch, vec, emb.model_name
                                    )
                            except Exception as e:
                                logger.debug(
                                    "Library write-through failed for fragment %s: %s",
                                    frag["id"], e,
                                )
                    else:
                        failed += 1
                except Exception as e:
                    console.print(f"  [red]Fragment {frag['id']}: {e}[/red]")
                    failed += 1
            progress.advance(task)

    console.print(f"\n[green]Embedded: {embedded}[/green]", end="")
    if lib_hits:
        console.print(f" | [dim]Library cache: {lib_hits}[/dim]", end="")
    if failed:
        console.print(f" | [red]Failed: {failed}[/red]", end="")
    console.print()


@embed.command(name="sections")
@click.option("--dry-run", is_flag=True, help="Preview without writing to DB")
@click.option(
    "--backend",
    type=click.Choice(["s2", "local", "openai", "litellm"]),
    help="Override embedding backend",
)
@click.pass_context
def embed_sections(ctx, dry_run, backend):
    """Compute section centroid embeddings from source vectors.

    Centroids are re-computed from the source vectors already stored, so
    ``--remodel`` at this layer would be a no-op — re-run ``embed sources
    --remodel`` first and then invoke ``embed sections``.
    """
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
    type=click.Choice(["s2", "local", "openai", "litellm"]),
    help="Override embedding backend",
)
@click.option(
    "--remodel",
    is_flag=True,
    help="Forward --remodel to the sources and fragments sub-steps",
)
@click.pass_context
def embed_all(ctx, dry_run, backend, remodel):
    """Run sources \u2192 fragments \u2192 sections in sequence."""
    console.print("[dim]Step 1/3: sources[/dim]")
    ctx.invoke(embed_sources, dry_run=dry_run, backend=backend, remodel=remodel)
    console.print("\n[dim]Step 2/3: fragments[/dim]")
    ctx.invoke(embed_fragments, dry_run=dry_run, backend=backend, remodel=remodel)
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
