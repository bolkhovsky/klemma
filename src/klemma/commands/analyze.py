"""Status, coach, and suggest commands."""

import click
from rich.table import Table

from ..cli import (
    _get_context,
    _lookup_section_type,
    _print_recommended_actions,
    _print_ref_gaps_table,
    _sync_sections,
    console,
    main,
)


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="Show full detailed tables")
@click.option("--chapter", "-ch", type=int, help="Filter by chapter")
@click.option(
    "--degraded",
    "show_degraded",
    is_flag=True,
    help="Показать деградированные источники и их непройденные шаги",
)
@click.pass_context
def status(ctx, verbose, chapter, show_degraded):
    """Unified status: processing, coverage, gaps, reference gaps."""
    kctx = _get_context(ctx)
    cfg, state = kctx.config, kctx.state
    project = kctx.project

    if show_degraded:
        _print_degraded_sources(state)
        return

    _sync_sections(kctx, quiet=True)

    proc_stats = state.get_stats()
    frag_stats = state.get_fragment_stats()
    # Prefer project.db coverage stats (ADR-014 Phase 1D); fall back to monolithic DB
    _ps = kctx.project_store
    cov = (
        _ps.get_coverage_stats()
        if _ps and _ps.count_sources() > 0
        else state.get_coverage_stats()
    )

    # --- Processing summary ---
    completed = proc_stats.get("completed", 0)
    pending = proc_stats.get("pending", 0)
    failed = proc_stats.get("failed", 0)
    skipped = proc_stats.get("skipped", 0)
    degraded = proc_stats.get("degraded", 0)
    total = proc_stats.get("total", 0)
    parts = [f"[green]{completed} completed[/green]"]
    if degraded:
        parts.append(f"[yellow]{degraded} degraded[/yellow]")
    if skipped:
        parts.append(f"[dim]{skipped} skipped[/dim]")
    if pending:
        parts.append(f"[yellow]{pending} pending[/yellow]")
    if failed:
        parts.append(f"[red]{failed} failed[/red]")
    console.print(
        f"Processing: {' | '.join(parts)}  [dim]({total} total, {frag_stats.get('total', 0)} fragments)[/dim]"
    )
    if degraded:
        console.print(
            "[dim]Run 'klemma status --degraded' for details, "
            "'klemma repair' to fix.[/dim]"
        )
    console.print()

    # --- Coverage (chapter-based for dissertation/thesis, simple for paper) ---
    has_chapters = project and project.chapters and project.type != "paper"

    if has_chapters:
        chapter_numbers = project.chapter_numbers
        type_lookup = cov.get("section_type_lookup", {})
        table = Table(title="Coverage by Chapter", show_edge=False, pad_edge=False)
        table.add_column("Chapter", style="cyan")
        table.add_column("Type", style="dim")
        table.add_column("Sources", justify="right", width=8)
        for ch in chapter_numbers:
            if chapter and ch != chapter:
                continue
            count = cov["chapters"].get(ch, 0)
            style = "green" if count >= 10 else "yellow" if count >= 5 else "red"
            name = project.chapters.get(ch, "")
            stype = type_lookup.get(str(ch), "")
            table.add_row(f"Ch {ch}: {name}", stype, f"[{style}]{count}[/{style}]")
        console.print(table)

        # Sections (verbose or filtered by chapter)
        if (verbose or chapter) and cov["sections"]:
            console.print()
            type_lookup = cov.get("section_type_lookup", {})
            sec_table = Table(
                title="Coverage by Section", show_edge=False, pad_edge=False
            )
            sec_table.add_column("Section", style="cyan")
            sec_table.add_column("Type", style="dim")
            sec_table.add_column("Sources", justify="right", width=8)
            for sec, count in sorted(cov["sections"].items()):
                if chapter and not sec.startswith(f"{chapter}."):
                    continue
                style = "green" if count >= 3 else "yellow" if count >= 1 else "red"
                stype = _lookup_section_type(sec, type_lookup)
                sec_table.add_row(sec, stype, f"[{style}]{count}[/{style}]")
            console.print(sec_table)
    elif not project or project.type == "paper":
        # Paper: show section coverage if any, no chapter structure
        if cov["sections"]:
            type_lookup = cov.get("section_type_lookup", {})
            sec_table = Table(
                title="Coverage by Section", show_edge=False, pad_edge=False
            )
            sec_table.add_column("Section", style="cyan")
            sec_table.add_column("Type", style="dim")
            sec_table.add_column("Sources", justify="right", width=8)
            for sec, count in sorted(cov["sections"].items()):
                style = "green" if count >= 3 else "yellow" if count >= 1 else "red"
                stype = _lookup_section_type(sec, type_lookup)
                sec_table.add_row(sec, stype, f"[{style}]{count}[/{style}]")
            console.print(sec_table)
    else:
        # Fallback: legacy dissertation config
        chapter_numbers = list(range(1, 5))
        type_lookup = cov.get("section_type_lookup", {})
        table = Table(title="Coverage by Chapter", show_edge=False, pad_edge=False)
        table.add_column("Chapter", style="cyan")
        table.add_column("Type", style="dim")
        table.add_column("Sources", justify="right", width=8)
        for ch in chapter_numbers:
            count = cov["chapters"].get(ch, 0)
            style = "green" if count >= 10 else "yellow" if count >= 5 else "red"
            name = cfg.dissertation.chapters.get(ch, "")
            stype = type_lookup.get(str(ch), "")
            table.add_row(f"Ch {ch}: {name}", stype, f"[{style}]{count}[/{style}]")
        console.print(table)

    # --- Coverage by semantic type ---
    section_types = cov.get("section_types", {})
    if section_types:
        console.print()
        type_parts = []
        for st_name, st_count in sorted(section_types.items()):
            style = "green" if st_count >= 10 else "yellow" if st_count >= 5 else "dim"
            type_parts.append(f"[{style}]{st_name} {st_count}[/{style}]")
        console.print(f"[bold]By type:[/bold] {' | '.join(type_parts)}")

    # --- Top gaps ---
    min_sources = (
        project.min_sources_per_section
        if project
        else cfg.dissertation.min_sources_per_section
    )
    gaps_data = state.get_gaps(min_sources=min_sources)
    if gaps_data:
        if chapter:
            gaps_data = [g for g in gaps_data if g["section"].startswith(f"{chapter}.")]
        shown = gaps_data if verbose else gaps_data[:5]
        console.print()
        console.print(
            f"[bold]Top Gaps[/bold] [dim](sections with < {min_sources} sources)[/dim]"
        )
        for gap in shown:
            needed = min_sources - gap["count"]
            console.print(
                f"  [red]{gap['section']}[/red] — {gap['count']} sources [dim](need {needed} more)[/dim]"
            )
        if not verbose and len(gaps_data) > 5:
            console.print(
                f"  [dim]... and {len(gaps_data) - 5} more (use --verbose)[/dim]"
            )

    # --- Reference gaps ---
    _sw = kctx.project.section_weights if kctx.project else None
    if verbose:
        _print_ref_gaps_table(
            state, limit=20, embeddings=kctx.embeddings, section_weights=_sw
        )
    else:
        ref_gaps = state.get_reference_gaps(limit=5, section_weights=_sw)
        if ref_gaps:
            gap_summary = state.get_gap_summary()
            console.print()
            console.print(
                f"[bold]Ref Gaps[/bold] [dim]({gap_summary['open_count']} open)[/dim]"
            )
            for g in ref_gaps:
                year = g.get("ref_year") or ""
                console.print(
                    f"  [yellow]x{g['count']}[/yellow]  {(g['ref_authors'] or '')[:20]} ({year}) "
                    f"[dim]— {(g.get('why_relevant') or '')[:40]}[/dim]"
                )

    # --- Verbose: fragment breakdown ---
    if verbose and frag_stats["total"] > 0:
        console.print()
        ft = Table(title="Fragment Distribution", show_edge=False, pad_edge=False)
        ft.add_column("Category", style="cyan")
        ft.add_column("Count", justify="right")
        for ftype, cnt in sorted(frag_stats["by_type"].items()):
            ft.add_row(ftype, str(cnt))
        console.print(ft)

    # --- Verbose: intent coverage matrix ---
    if verbose:
        intent_cov = state.get_intent_coverage()
        if intent_cov:
            console.print()
            it = Table(title="Intent Coverage", show_edge=False, pad_edge=False)
            it.add_column("Section", style="cyan")
            it.add_column("Background", justify="right")
            it.add_column("Method", justify="right")
            it.add_column("Result", justify="right")
            it.add_column("Total", justify="right", style="bold")
            for sec in sorted(intent_cov):
                if chapter and not sec.startswith(f"{chapter}."):
                    continue
                d = intent_cov[sec]
                it.add_row(
                    sec,
                    str(d["background"]) if d["background"] else "[dim]0[/dim]",
                    str(d["method"]) if d["method"] else "[dim]0[/dim]",
                    (
                        str(d["result_comparison"])
                        if d["result_comparison"]
                        else "[dim]0[/dim]"
                    ),
                    str(d["total"]),
                )
            console.print(it)

    # --- Verbose: embedding stats ---
    if verbose:
        emb_stats = state.get_embedding_stats()
        if emb_stats["embedded"] > 0 or emb_stats["total"] > 0:
            console.print()
            pct = (
                (emb_stats["embedded"] / emb_stats["total"] * 100)
                if emb_stats["total"]
                else 0
            )
            console.print(
                f"[bold]Embeddings[/bold]: {emb_stats['embedded']}/{emb_stats['total']} "
                f"sources ({pct:.0f}%)"
            )
            for model, cnt in emb_stats["models"].items():
                console.print(f"  [dim]{model}: {cnt}[/dim]")

            # Section embeddings
            sec_stats = state.get_section_embedding_stats()
            if sec_stats["total_sections"] > 0:
                sec_emb = sec_stats["embedded_sections"]
                sec_total = sec_stats["total_sections"]
                sec_pct = sec_emb / sec_total * 100
                missing = sec_total - sec_emb
                line = (
                    f"[bold]Section embeddings[/bold]: {sec_emb}/{sec_total} "
                    f"sections ({sec_pct:.0f}%)"
                )
                if missing:
                    line += f"  [yellow]{missing} missing[/yellow]"
                console.print(line)
                for model, cnt in sec_stats["models"].items():
                    console.print(f"  [dim]{model}: {cnt}[/dim]")

    # --- Verbose: citation graph stats ---
    if verbose:
        graph = state.get_citation_graph_stats()
        if graph["total_links"] > 0:
            console.print()
            console.print(
                f"[bold]Citation Graph[/bold]: {graph['total_links']} links, "
                f"{graph['unique_targets']} unique targets "
                f"({graph['in_library']} in library, {graph['external']} external)"
            )
            console.print(
                f"  [dim]{graph['source_count']} citing sources, "
                f"avg {graph['avg_refs_per_source']} refs/source[/dim]"
            )
            if graph["most_cited_external"]:
                console.print()
                console.print(
                    "[bold]Most Cited External[/bold] [dim](bridging nodes)[/dim]"
                )
                for ref in graph["most_cited_external"][:5]:
                    authors = (ref["target_authors"] or "")[:25]
                    year = ref["target_year"] or ""
                    console.print(
                        f"  [yellow]x{ref['cite_count']}[/yellow]  "
                        f"{authors} ({year}) [dim]{(ref['target_title'] or '')[:40]}[/dim]"
                    )
            if graph["most_connected_internal"]:
                console.print()
                console.print("[bold]Most Connected Internal[/bold]")
                for ref in graph["most_connected_internal"][:5]:
                    console.print(
                        f"  [green]x{ref['cite_count']}[/green]  @{ref['target_citekey']}"
                    )

    # --- Author publications (ГОСТ Р 7.0.11-2011) ---
    author_counts = state.get_author_publication_counts()
    if author_counts:
        from ..source_role import ROLE_LABELS, format_gost_phrase

        console.print()
        console.print("[bold]Публикации автора[/bold]")
        for role, cnt in sorted(author_counts.items()):
            label = ROLE_LABELS.get(role, role)
            console.print(f"  {label}: [green]{cnt}[/green]")
        gost = format_gost_phrase(author_counts)
        if gost:
            console.print(f"\n  [dim italic]{gost}[/dim italic]")

    # --- Recommended actions ---
    _emb = state.get_embedding_stats()
    _prune = state.get_prune_summary()
    _ref = state.get_reference_gaps(limit=3, section_weights=_sw)
    _print_recommended_actions(proc_stats, _emb, gaps_data, _ref, _prune)


def _print_degraded_sources(state) -> None:
    """List degraded sources with their silently-failed pipeline steps."""
    degraded = state.get_degraded_sources()
    if not degraded:
        console.print("[green]No degraded sources.[/green]")
        return

    table = Table(
        title=f"Degraded sources ({len(degraded)})",
        show_edge=False,
        pad_edge=False,
    )
    table.add_column("Source", style="cyan")
    table.add_column("Failed steps", style="yellow")
    for row in degraded:
        table.add_row(f"@{row['id']}", ", ".join(row["degraded_steps"]) or "—")
    console.print(table)
    console.print("\n[dim]Fix with: klemma repair <CITEKEY> (or bare 'klemma repair')[/dim]")


# Backward-compatible aliases
@main.command(hidden=True)
@click.pass_context
def stats(ctx):
    """[alias] -> status"""
    ctx.invoke(status)


@main.command(hidden=True)
@click.pass_context
def coverage(ctx):
    """[alias] -> status --verbose"""
    ctx.invoke(status, verbose=True)


@main.command()
@click.option("--limit", "-n", type=int, default=10, help="Number of suggestions")
@click.option("--section", "-s", default=None, help="Filter by section (e.g. 1.3)")
@click.pass_context
def suggest(ctx, limit, section):
    """Suggest papers to fill reference gaps."""
    from ..search import (
        ChainSearchProvider,
        CrossRefSearchProvider,
        S2SearchProvider,
        create_search,
    )
    from ..skills.suggester import suggest_acquisitions

    kctx = _get_context(ctx)
    _sync_sections(kctx, quiet=True)

    # Fetch more gaps than needed (some won't resolve)
    gaps_list = kctx.state.get_reference_gaps(section=section, limit=limit * 3)

    if not gaps_list:
        console.print("[yellow]No open reference gaps found.[/yellow]")
        return

    # Initialize search: configured provider, or default S2 -> CrossRef chain
    search = kctx.search
    if search is None:
        search_cfg = kctx.config.search
        if search_cfg.backend:
            search = create_search(search_cfg.model_dump())
        else:
            search = ChainSearchProvider(
                [
                    CrossRefSearchProvider(),
                    S2SearchProvider(),
                ]
            )

    console.print(f"\n[bold]Resolving top gaps via {search.backend_name}...[/bold]\n")

    suggest_cfg = kctx.config.suggest
    candidates, filtered_old = suggest_acquisitions(
        gaps_list,
        search,
        limit=limit,
        max_age_years=suggest_cfg.max_age_years,
        classic_min_score=suggest_cfg.classic_min_score,
    )

    if not candidates:
        msg = "[yellow]No gaps could be resolved.[/yellow]"
        if filtered_old:
            msg += f"\n[dim]{filtered_old} older papers filtered (>{suggest_cfg.max_age_years}y)[/dim]"
        console.print(msg)
        return

    total_open = len(gaps_list)
    table = Table(
        title=f"Gap Suggestions ({len(candidates)} of {total_open} open gaps)",
        show_lines=True,
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Score", justify="right", width=6)
    table.add_column("Authors", width=20)
    table.add_column("Year", width=5)
    table.add_column("Title", width=35)
    table.add_column("Sections", width=12)

    for i, c in enumerate(candidates, 1):
        year_str = str(c.ref_year) if c.ref_year else "\u2014"
        sections_str = ", ".join(c.sections) if c.sections else "\u2014"
        title_display = (
            c.ref_title[:60] + "..." if len(c.ref_title) > 60 else c.ref_title
        )

        table.add_row(
            str(i),
            f"{c.score:.1f}",
            c.ref_authors[:25] if c.ref_authors else "\u2014",
            year_str,
            title_display,
            sections_str,
        )

    console.print(table)

    # Print acquire commands below the table
    console.print()
    for i, c in enumerate(candidates, 1):
        if c.acquire_cmd:
            console.print(f"  [dim]{i}.[/dim] [green]\u2192 {c.acquire_cmd}[/green]")
        elif c.doi:
            console.print(
                f"  [dim]{i}.[/dim] [yellow]\u26a0 No open-access PDF found"
                f" (DOI: {c.doi})[/yellow]"
            )
        else:
            console.print(f"  [dim]{i}.[/dim] [dim]\u26a0 Not found in search API[/dim]")
    if filtered_old:
        console.print(
            f"  [dim]{filtered_old} older papers filtered (>{suggest_cfg.max_age_years}y, score<{suggest_cfg.classic_min_score})[/dim]"
        )
    console.print()


# Backward-compat: `klemma gaps` group with `suggest` subcommand
@main.group(invoke_without_command=True)
@click.pass_context
def gaps(ctx):
    """Reference gaps and acquisition suggestions."""
    if ctx.invoked_subcommand is None:
        console.print(
            "[yellow]Warning: `klemma gaps` is deprecated. Use `klemma status --verbose`.[/yellow]"
        )
        ctx.invoke(status, verbose=True)


main.add_command(gaps)


# Keep options in sync with top-level suggest
@gaps.command(name="suggest", hidden=True)
@click.option("--limit", "-n", type=int, default=10)
@click.option("--section", "-s", default=None)
@click.pass_context
def gaps_suggest(ctx, limit, section):
    """[alias] -> suggest"""
    ctx.invoke(suggest, limit=limit, section=section)


# Source group with role subcommand
@main.group(invoke_without_command=True)
@click.pass_context
def source(ctx):
    """Manage individual sources."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


main.add_command(source)


@source.command()
@click.argument("citekey")
@click.argument(
    "role",
    type=click.Choice(
        [
            "external",
            "author_vak",
            "author_scopus",
            "author_wos",
            "author_conf",
            "author_patent",
            "author_program",
            "author_other",
        ]
    ),
)
@click.pass_context
def role(ctx, citekey, role):
    """Assign a source_role to a source (e.g. author_vak, author_conf)."""
    kctx = _get_context(ctx)
    src = kctx.state.get_source(citekey)
    if not src:
        console.print(f"[red]Source '{citekey}' not found.[/red]")
        raise SystemExit(1)
    kctx.state.set_source_role(citekey, role)
    from ..source_role import ROLE_LABELS

    label = ROLE_LABELS.get(role, role)
    console.print(f"[green]@{citekey}[/green] \u2192 {label}")


@source.command()
@click.argument("citekey")
@click.option("--run", "run_id", type=int, default=None, help="Show the snapshot of one extraction run")
@click.option("--all-runs", is_flag=True, help="Show every project fragment with the runs that produced it")
@click.option("--notes", "show_notes", is_flag=True, help="Show the latest run's structure notes (exhaustive mode)")
@click.pass_context
def show(ctx, citekey, run_id, all_runs, show_notes):
    """Display full source card: metadata, sections, fragments, extraction runs."""
    kctx = _get_context(ctx)
    src = kctx.state.get_source(citekey)
    if not src:
        console.print(f"[red]Source '{citekey}' not found.[/red]")
        raise SystemExit(1)

    # Header
    title = src.get("title") or "(no title)"
    authors = src.get("authors") or "(no authors)"
    year = src.get("year") or "?"
    console.print(f"\n[bold]@{citekey}[/bold]")
    console.print(f"  [cyan]{title}[/cyan]")
    console.print(f"  {authors} ({year})")

    # Metadata
    doi = src.get("doi")
    if doi:
        console.print(f"  DOI: {doi}")
    status = src.get("status", "?")
    console.print(f"  Status: {status}")
    source_role = src.get("source_role")
    if source_role and source_role != "external":
        from ..source_role import ROLE_LABELS
        console.print(f"  Role: {ROLE_LABELS.get(source_role, source_role)}")
    priority = src.get("citation_priority")
    if priority:
        console.print(f"  Priority: {priority}")
    quality = src.get("quality_score")
    if quality is not None:
        console.print(f"  Quality: {quality}")
    nr1 = src.get("relevance_nr1", 0)
    nr2 = src.get("relevance_nr2", 0)
    if nr1 or nr2:
        console.print(f"  Relevance: NR1={nr1} NR2={nr2}")

    # Sections
    fragments = kctx.state.get_fragments(source_id=citekey, limit=500)
    sections = sorted({f["section"] for f in fragments if f.get("section")})
    if sections:
        console.print(f"  Sections: {', '.join(sections)}")

    # Paths
    pdf = src.get("pdf_path")
    if pdf:
        console.print(f"  PDF: {pdf}")
    note = src.get("note_path")
    if note:
        console.print(f"  Note: {note}")

    # Extraction runs (plan C2): active set lives in project.db
    pj = getattr(kctx, "project_store", None)
    if pj is not None:
        try:
            runs = pj.get_runs(citekey)
            active = pj.get_active_run_id(citekey)
        except Exception:  # noqa: BLE001 — stores may be absent in tests
            runs, active = [], None
        if runs:
            console.print(f"\n  [bold]Runs[/bold] (active: {active if active is not None else 'legacy'})")
            for r in runs:
                mark = "*" if r["run_id"] == active else " "
                flags = []
                if r.get("is_partial"):
                    flags.append("partial")
                if r.get("validation_incomplete"):
                    flags.append("unvalidated")
                console.print(
                    f"   {mark} #{r['run_id']} {r.get('started_at', '')[:16]} {r.get('mode', '')} "
                    f"{r.get('ai_model', '')} chunks={r.get('chunk_count', 0)} "
                    f"frags={r.get('fragment_count', 0)} {r['status']}"
                    + (f" [{', '.join(flags)}]" if flags else "")
                    + (f" — {r['error']}" if r.get("error") else "")
                )
        if show_notes and runs:
            import json as _json

            target = next((r for r in reversed(runs) if r.get("notes_json")), None)
            if target is None:
                console.print("\n  [dim]No structure notes recorded (run with --exhaustive)[/dim]")
            else:
                notes = _json.loads(target["notes_json"])
                console.print(f"\n  [bold]Structure notes[/bold] (run #{target['run_id']})")
                for key, label in (("contradicts", "contradicts"), ("qualifies", "qualifies")):
                    for n in notes.get(key, []) or []:
                        st = "" if n.get("status") == "confirmed" else " [dim](unverified)[/dim]"
                        console.print(f"   {label} {n.get('item', '?')}{st}: «{(n.get('quote') or '')[:100]}»")
                        if n.get("note"):
                            console.print(f"      {n['note'][:160]}")
                ne = notes.get("not_extracted") or []
                if ne:
                    console.print(f"   not_extracted ({len(ne)}): {', '.join(ne[:40])}{' …' if len(ne) > 40 else ''}")
        if run_id is not None or all_runs:
            rows = pj.get_project_fragments(citekey, run_id=run_id, all_runs=all_runs)
            label = f"run #{run_id}" if run_id is not None else "all runs"
            console.print(f"\n  [bold]Project fragments[/bold] ({label}): {len(rows)}")
            tbl = Table(show_header=True, box=None, padding=(0, 1))
            tbl.add_column("fragment_id", style="dim", width=12)
            tbl.add_column("section", width=8)
            tbl.add_column("origin", width=14)
            tbl.add_column("runs", width=10)
            for row in rows:
                eff = row.get("curated_section") or row.get("run_model_section") or row.get("section") or row.get("legacy_section") or "-"
                tbl.add_row(
                    str(row.get("fragment_id", ""))[:12], eff, row.get("section_origin") or "-",
                    str(row.get("run_ids") or (run_id if run_id is not None else "")),
                )
            console.print(tbl)

    # Fragments
    frag_count = src.get("fragment_count", 0) or len(fragments)
    console.print(f"\n  [bold]Fragments[/bold]: {frag_count}")
    if fragments:
        tbl = Table(show_header=True, box=None, padding=(0, 1))
        tbl.add_column("#", style="dim", width=4)
        tbl.add_column("Section", width=8)
        tbl.add_column("Type", width=10)
        tbl.add_column("Text", max_width=80)
        for i, f in enumerate(fragments, 1):
            text = (f.get("fragment_text") or "")[:120]
            if len(f.get("fragment_text") or "") > 120:
                text += "..."
            tbl.add_row(
                str(i),
                f.get("section") or "-",
                f.get("fragment_type") or "-",
                text,
            )
        console.print(tbl)
    console.print()


@source.command(name="select")
@click.option("--max-fragments", type=int, default=None, help="Sources with at most N fragments (the 1..N band)")
@click.option("--min-quality", type=int, default=None, help="Quality score ≥ Q applies to the 1..N band")
@click.option("--include-zero/--no-include-zero", default=True, help="Include sources with zero fragments")
@click.option("--exclude-prune-drop/--no-exclude-prune-drop", default=True)
@click.option("--with-pdf", is_flag=True, help="Only sources whose PDF is resolvable")
@click.option("--status", "statuses", default="completed,degraded,skipped,pending,failed",
              help="Comma-separated source statuses")
@click.option("--exclude-title-regex", default=None, help="Drop sources whose title matches")
@click.option("--format", "fmt", type=click.Choice(["citekeys", "table"]), default="citekeys")
@click.pass_context
def select(ctx, max_fragments, min_quality, include_zero, exclude_prune_drop, with_pdf,
           statuses, exclude_title_regex, fmt):
    """Select sources for (re)processing — feeds `klemma process --from-file`."""
    import re as _re
    from pathlib import Path

    kctx = _get_context(ctx)
    state = kctx.state
    wanted = {s.strip() for s in statuses.split(",") if s.strip()}
    drop_ids: set[str] = set()
    if exclude_prune_drop:
        try:
            drop_ids = set(state.prune.get_prune_drop_ids())
        except Exception:  # noqa: BLE001
            drop_ids = set()
        pj = getattr(kctx, "project_store", None)
        if pj is not None:
            try:  # the librarian writes verdicts to project.db when it is available
                drop_ids |= set(pj.get_prune_drop_ids())
            except Exception:  # noqa: BLE001
                pass
    title_re = _re.compile(exclude_title_regex, _re.IGNORECASE) if exclude_title_regex else None
    pdf_lookup = dict(getattr(kctx.library, "pdf_paths", {}) or {}) if kctx.library else {}

    rows = []
    for src in state.get_sources_for_selection():
        ck = src["id"]
        if src.get("status") not in wanted or ck in drop_ids:
            continue
        title = src.get("title") or ""
        if title_re and title_re.search(title):
            continue
        n = int(src.get("fragment_count") or 0)
        q = src.get("quality_score")
        if n == 0:
            if not include_zero:
                continue
        else:
            if max_fragments is None or n > max_fragments:
                continue
            if min_quality is not None and (q is None or int(q) < min_quality):
                continue
        if with_pdf:
            p = src.get("pdf_path") or pdf_lookup.get(ck)
            if not p or not Path(str(p)).exists():
                continue
        rows.append((ck, n, q, src.get("status"), title[:60]))

    if fmt == "citekeys":
        for ck, *_ in rows:
            click.echo(ck)
        return
    tbl = Table(show_header=True, box=None, padding=(0, 1))
    for col in ("citekey", "frags", "q", "status", "title"):
        tbl.add_column(col)
    for ck, n, q, st, title in rows:
        tbl.add_row(ck, str(n), str(q if q is not None else "-"), st or "-", title)
    console.print(tbl)
    console.print(f"[dim]{len(rows)} source(s)[/dim]")
