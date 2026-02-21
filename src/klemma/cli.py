"""Klemma CLI — AI academic assistant."""

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__, get_banner
from .ai import create_ai
from .config import (
    discover_project_chain,
    discover_project_root,
    ensure_system_home,
    load_available_tags,
    load_config,
    load_project_context,
    resolve_effective_config,
)
from .context import KlemmaContext
from .library_provider import create_library
from .state import StateManager
from .tools.registry import ToolRegistry
from .vault import VaultAdapter

console = Console()


def _init_components(config_path: str | None = None) -> KlemmaContext:
    """Initialize all components by discovering project from cwd.

    Uses Git-style .klemma/ discovery. If config_path is given, uses it directly.
    """
    system_home = ensure_system_home()

    if config_path:
        # Explicit --config: load directly, skip discovery
        cfg = load_config(config_path)
        project = cfg.project
        if not project:
            from .config import ProjectConfig, DissertationConfig
            if cfg.dissertation and cfg.dissertation.title:
                project = ProjectConfig.from_dissertation(cfg.dissertation)
            else:
                project = ProjectConfig()
        project_root = Path(config_path).resolve().parent
        if project_root.name == ".klemma":
            project_root = project_root.parent
        project_chain = [project_root]
        klemma_home = project_root / ".klemma"
        if not klemma_home.is_dir():
            klemma_home = system_home
    else:
        # Git-style discovery: find .klemma/ by traversing up from cwd
        project_chain = discover_project_chain()
        if not project_chain:
            raise click.ClickException(
                "Not in a klemma project. Run 'klemma init' to create one here."
            )
        cfg, project, project_root = resolve_effective_config(project_chain)
        klemma_home = project_root / ".klemma"

    # Resolve db_path relative to project's .klemma/ directory
    db_path = cfg.state.db_path
    if not Path(db_path).is_absolute():
        db_path = str(klemma_home / db_path)

    state = StateManager(db_path)
    vault = VaultAdapter(cfg.obsidian.vault_path, use_cli=cfg.obsidian.use_cli)
    library = create_library(cfg)
    tools = ToolRegistry(cfg) if cfg.mcp.servers else None

    dissertation_context = load_project_context(project_chain, cfg)
    available_tags = load_available_tags(klemma_home, cfg)

    return KlemmaContext(
        config=cfg, state=state, vault=vault, library=library, tools=tools,
        project=project, project_name=project_root.name,
        klemma_home=klemma_home,
        dissertation_context=dissertation_context,
        available_tags=available_tags,
        project_root=project_root,
        project_chain=project_chain,
        system_home=system_home,
    )


def _init_ai(cfg):
    """Initialize AI client (separate to allow commands without API key)."""
    return create_ai(cfg.ai)


def _sync_sections(ctx: KlemmaContext, quiet=False) -> dict:
    """Sync section assignments from vault frontmatter + discover new Zotero entries.

    Fast (~60ms for 138 notes). Safe to call on every command.
    """
    cfg, state, vault = ctx.config, ctx.state, ctx.vault
    from .literature.note_factory import auto_classify

    notes_folder = cfg.obsidian.notes_folder
    note_names = vault.list_notes(notes_folder)

    # 1. Parse vault frontmatter for all @citekey notes
    vault_data = []
    for note_name in note_names:
        if not note_name.startswith("@"):
            continue
        citekey = note_name.lstrip("@")
        props = vault.get_properties(note_name)
        if not props:
            continue

        chapter = props.get("chapter")
        if isinstance(chapter, str):
            chapter = int(chapter) if chapter.isdigit() else None

        quality = props.get("quality", 0)
        if isinstance(quality, str):
            quality = int(quality.split("/")[0]) if "/" in quality else int(quality)

        sections_list = props.get("sections", [])
        chapters_list = props.get("chapters", [])

        vault_data.append({
            "citekey": citekey,
            "primary_section": str(props.get("section", "")) or None,
            "primary_chapter": chapter,
            "sections": [str(s) for s in sections_list] if isinstance(sections_list, list) else [],
            "chapters": [int(c) for c in chapters_list] if isinstance(chapters_list, list) else [],
            "quality": quality or 0,
            "priority": props.get("priority", "medium"),
            "nr1": props.get("relevance_nr1", 0) or 0,
            "nr2": props.get("relevance_nr2", 0) or 0,
            "note_path": f"{notes_folder}/{note_name}.md",
        })

    # 2. Discover new Zotero entries not in DB + detect renames
    new_entries = []
    renames = []
    if ctx.library:
        entry_lookup = ctx.library.entries
        vault_citekeys = {vd["citekey"] for vd in vault_data}

        with state._conn() as conn:
            cur = conn.execute("SELECT id FROM sources")
            existing = {row["id"] for row in cur.fetchall()}

        # Rename detection via immutable Zotero itemKey
        db_zotero_keys = state.get_zotero_key_map()  # {itemKey: old_citekey}
        for citekey, entry in entry_lookup.items():
            if citekey in existing:
                continue
            if entry.item_key and entry.item_key in db_zotero_keys:
                old_ck = db_zotero_keys[entry.item_key]
                if old_ck != citekey:
                    state.rename_source(old_ck, citekey, entry.item_key)
                    existing.discard(old_ck)
                    existing.add(citekey)
                    renames.append((old_ck, citekey))
                    continue
            if citekey not in vault_citekeys:
                classification = auto_classify(entry, cfg)
                new_entries.append((citekey, classification))

        # Fuzzy orphan cleanup: DB sources not in BBT JSON (pre-existing renames)
        bbt_citekeys = set(entry_lookup.keys())
        orphans = existing - bbt_citekeys
        if orphans:
            import re
            bbt_by_author_year: dict[tuple[str, str], tuple[str, str]] = {}
            for ck, entry in entry_lookup.items():
                am = re.match(r"([a-z.]+?)(?=[A-Z\d])", ck)
                ym = re.search(r"(\d{4})", ck)
                if am and ym:
                    author = am.group(1).replace(".", "").lower()
                    bbt_by_author_year[(author, ym.group(1))] = (ck, entry.item_key or "")

            for old_ck in orphans:
                clean = re.sub(r"^[a-z]\.[a-z]\.", "", old_ck)
                am = re.match(r"([a-z.]+?)(?=[A-Z\d])", clean)
                ym = re.search(r"(\d{4})", old_ck)
                if not (am and ym):
                    continue
                author = am.group(1).replace(".", "").lower()
                match = bbt_by_author_year.get((author, ym.group(1)))
                if not match:
                    continue
                new_ck, item_key = match
                if new_ck in existing:
                    # New key already exists with data — delete orphan duplicate
                    state.delete_source(old_ck)
                    existing.discard(old_ck)
                    renames.append((old_ck, new_ck))
                else:
                    state.rename_source(old_ck, new_ck, item_key)
                    existing.discard(old_ck)
                    existing.add(new_ck)
                    renames.append((old_ck, new_ck))

        # Backfill zotero_key for existing sources (idempotent, only fills NULL)
        backfill = {ck: entry.item_key for ck, entry in entry_lookup.items() if entry.item_key}
        if backfill:
            state.populate_zotero_keys(backfill)

    # 3. Sync to DB
    result = state.sync_source_sections(vault_data, new_entries)

    if not quiet:
        parts = []
        if renames:
            parts.append(f"[magenta]{len(renames)} renamed[/magenta]")
            for old_ck, new_ck in renames:
                console.print(f"  [magenta]Rename:[/magenta] @{old_ck} → @{new_ck}")
        if result["vault_updated"]:
            parts.append(f"[green]{result['vault_updated']} updated from vault[/green]")
        if result["new_registered"]:
            parts.append(f"[blue]{result['new_registered']} new from Zotero[/blue]")
        if parts:
            console.print("[dim]Sync:[/dim] " + " | ".join(parts))

    return result


def _print_status_line(state: StateManager, project_name: str = "default"):
    """Print a compact status line with key metrics."""
    try:
        stats = state.get_stats()
        frag_stats = state.get_fragment_stats()
        parts = [
            f"[dim]{stats.get('total', 0)} sources[/dim]",
            f"[dim]{frag_stats.get('total', 0)} fragments[/dim]",
        ]
        if project_name != "default":
            parts.insert(0, f"[cyan]{project_name}[/cyan]")
        gap_summary = state.get_gap_summary()
        if gap_summary["open_count"] > 0:
            top = ""
            if gap_summary["top_ref"]:
                top = f" (top: {gap_summary['top_ref']} x{gap_summary['top_count']})"
            parts.append(f"[yellow]{gap_summary['open_count']} ref-gaps{top}[/yellow]")
        prune = state.get_prune_summary()
        if prune["total"] > 0:
            parts.append(f"[yellow]{prune['total']} pruned ({prune['drop']} drop, {prune['maybe']} maybe)[/yellow]")
        console.print("[dim]|[/dim] " + " [dim]|[/dim] ".join(parts))
    except Exception:
        pass  # Don't crash on status line failure


def _print_ref_gaps_table(state: StateManager, limit: int = 20):
    """Print reference gaps as a Rich table."""
    ref_gaps = state.get_reference_gaps(limit=limit)
    if not ref_gaps:
        return
    gap_summary = state.get_gap_summary()
    ref_table = Table(
        title=f"Reference Gaps — {gap_summary['open_count']} open (missing from library)",
        show_edge=False, pad_edge=False,
    )
    ref_table.add_column("#", justify="right", style="dim", width=3)
    ref_table.add_column("Score", justify="right", width=6)
    ref_table.add_column("Count", justify="right", width=5)
    ref_table.add_column("Authors", width=20)
    ref_table.add_column("Year", width=5)
    ref_table.add_column("Title")
    ref_table.add_column("Why", max_width=30, style="dim")

    for i, g in enumerate(ref_gaps, 1):
        score_style = "red bold" if g["score"] >= 10 else "yellow" if g["score"] >= 5 else "dim"
        ref_table.add_row(
            str(i),
            f"[{score_style}]{g['score']:.1f}[/{score_style}]",
            str(g["count"]),
            (g["ref_authors"] or "")[:20],
            str(g.get("ref_year") or ""),
            g["ref_title"] or "",
            (g.get("why_relevant") or "")[:30],
        )
    console.print()
    console.print(ref_table)


@click.group(invoke_without_command=True)
@click.version_option(version=__version__)
@click.option("--config", "-c", default=None, help="Config file path (override project config)")
@click.pass_context
def main(ctx, config):
    """Klemma — AI academic assistant.

    Run a subcommand or use --help for usage info.
    Uses Git-style project discovery: run 'klemma init' in your project directory.
    """
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config

    # Banner
    console.print(get_banner(cwd=str(Path.cwd())))

    # Check for project (skip for init/info/tree/migrate)
    skip_check = {"init", "info", "tree", "migrate"}
    if (
        ctx.invoked_subcommand is not None
        and ctx.invoked_subcommand not in skip_check
        and config is None
        and discover_project_root() is None
    ):
        console.print(
            "[yellow]Not in a klemma project.[/yellow]\n"
            "Run [bold]klemma init[/bold] to create a project here, "
            "or use --config to specify a config file."
        )
        ctx.exit(1)
        return

    if ctx.invoked_subcommand is not None:
        # Print status line for CLI subcommands
        try:
            kctx = _init_components(config)
            _print_status_line(kctx.state, project_name=kctx.project_name)
        except Exception:
            pass

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command()
@click.option("--type", "-t", "project_type", default="dissertation",
              type=click.Choice(["dissertation", "paper", "thesis"]),
              help="Project type")
@click.option("--global-only", is_flag=True, help="Only create/update ~/.klemma/ system config")
@click.pass_context
def init(ctx, project_type, global_only):
    """Initialize a new klemma project in current directory.

    Creates .klemma/ and KLEMMA.md in the current directory.
    Also ensures ~/.klemma/ system config exists.

    \b
    Examples:
      klemma init                    # dissertation project
      klemma init --type paper       # paper project
      klemma init --global-only      # only create system config
    """
    from .setup import init_project, init_system

    system_home = ensure_system_home()

    if global_only:
        result = init_system(system_home)
        if result["created"]:
            console.print(f"[green]Created {system_home}/[/green]")
            for name in result["created"]:
                console.print(f"  + {name}")
        else:
            console.print(f"[dim]System config already exists at {system_home}/[/dim]")
        return

    # Ensure system config exists
    init_system(system_home)

    # Create project in current directory
    project_dir = Path.cwd()
    result = init_project(project_dir, project_type=project_type)

    if result["created"]:
        console.print(f"[green]Initialized klemma {project_type} project in {project_dir}/[/green]")
        for name in result["created"]:
            console.print(f"  + {name}")
    if result["skipped"]:
        for name in result["skipped"]:
            console.print(f"  [dim]~ {name} (already exists, skipped)[/dim]")

    console.print()
    console.print("[bold]Next steps:[/bold]")
    console.print(f"  1. Edit [cyan].klemma/config.yaml[/cyan] — set Zotero/Obsidian paths")
    console.print(f"  2. Edit [cyan]KLEMMA.md[/cyan] — describe your project")
    console.print(f"  3. Edit [cyan].klemma/tags.yaml[/cyan] — define your topic taxonomy")
    console.print("  4. Run [bold]klemma status[/bold] to verify")


@main.command()
@click.pass_context
def plan(ctx):
    """Daily plan — focus, recommendations, deadlines."""
    config_path = ctx.obj["config_path"]
    kctx = _init_components(config_path)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    ai = _init_ai(cfg)

    from .skills.planner import generate_morning_plan

    with console.status("Генерация утреннего брифинга", spinner="dots"):
        plan = generate_morning_plan(
            cfg, state, vault, ai, project=kctx.project,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
        )

    console.print()

    # Статус
    if plan.status_line:
        console.print(Panel(plan.status_line, border_style="blue"))

    # Интервенция
    if plan.intervention and plan.intervention != "NONE":
        style = {
            "CELEBRATION": "green",
            "FOCUS_REDIRECT": "yellow",
            "ESCALATION": "red",
            "DEADLINE_RISK": "yellow",
            "DEADLINE_CRITICAL": "red bold",
        }.get(plan.intervention, "yellow")
        console.print(f"[{style}]{plan.intervention}[/{style}]")

    # Фокус
    console.print(Panel(
        f"[bold]{plan.focus}[/bold]\n\n"
        f"[dim]Почему:[/dim] {plan.why}",
        title="Фокус сегодня",
        border_style="green",
    ))

    # Источники
    if plan.sources_needed:
        console.print(f"\n[cyan]Источники:[/cyan] {', '.join(plan.sources_needed)}")

    # Задача ассистента
    if plan.assistant_task:
        console.print(f"\n[blue]Задача ассистента:[/blue] {plan.assistant_task}")

    # Чтение
    if plan.reading_target:
        console.print(f"\n[dim]Чтение:[/dim] {plan.reading_target}")

    # Стратегические предложения
    if plan.strategy_suggestions:
        console.print("\n[yellow]Предложения по стратегии:[/yellow]")
        for s in plan.strategy_suggestions:
            console.print(f"  - {s}")

    # Прогресс
    if plan.progress_summary:
        console.print(f"\n[dim]{plan.progress_summary}[/dim]")

    # Записать брифинг в daily note
    daily_content = f"## Klemma Брифинг\n\n{plan.briefing_text}\n"
    vault.append_to_daily(daily_content)
    console.print("\n[dim]Брифинг добавлен в daily note.[/dim]")


@main.command()
@click.argument("citekeys", nargs=-1)
@click.option("--serial", is_flag=True, help="Disable parallel processing")
@click.pass_context
def process(ctx, citekeys, serial):
    """Process source(s): extract fragments, annotate, create vault note.

    With CITEKEY(s): process specified sources (parallel when >1).
    Without arguments: process all pending sources.
    """
    config_path = ctx.obj["config_path"]
    kctx = _init_components(config_path)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    ai = _init_ai(cfg)

    from .literature.pdf import PDFExtractor

    pdf_extractor = PDFExtractor(max_chars=cfg.ai.max_pdf_chars)

    # Auto-resolve previously detected reference gaps against current library
    resolved = state.resolve_gaps(kctx.library.entries)
    if resolved:
        console.print(f"[green]Auto-resolved {resolved} reference gap(s)[/green]")

    # Build citekey list: explicit or all pending
    if citekeys:
        keys = list(citekeys)
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
                    pool.submit(_process_single, ck, cfg, state, vault, ai, pdf_extractor, kctx.library, quiet=True,
                               dissertation_context=kctx.dissertation_context, available_tags=kctx.available_tags,
                               klemma_home=kctx.klemma_home): ck
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
        for idx, ck in enumerate(keys, 1):
            n_frags, status = results.get(ck, (0, "unknown"))
            if n_frags > 0:
                console.print(f"  [{idx}/{len(keys)}] @{ck} — [green]{n_frags} fragments[/green]")
                ok += 1
            else:
                console.print(f"  [{idx}/{len(keys)}] @{ck} — [red]{status}[/red]")
        console.print(f"\n[green]Done: {ok}/{len(keys)} processed (parallel, {elapsed:.0f}s).[/green]")
    else:
        processed = 0
        for idx, ck in enumerate(keys, 1):
            if len(keys) > 1:
                console.print(f"\n[bold][{idx}/{len(keys)}] {ck}[/bold]")
            n_frags, _ = _process_single(ck, cfg, state, vault, ai, pdf_extractor, kctx.library,
                                         dissertation_context=kctx.dissertation_context,
                                         available_tags=kctx.available_tags,
                                         klemma_home=kctx.klemma_home)
            if n_frags > 0:
                processed += 1
        if len(keys) > 1:
            console.print(f"\n[green]Done: {processed}/{len(keys)} processed.[/green]")


def _process_single(citekey, cfg, state, vault, ai, pdf_extractor, library, quiet=False,
                    dissertation_context="", available_tags=None, klemma_home=None):
    """Process a single source: find PDF, extract fragments, save to vault.

    Returns (fragment_count, status_message). When quiet=True, suppresses console output
    (used for parallel execution).
    """
    from .skills.extractor import extract_fragments, save_fragments_to_vault

    source = state.get_source(citekey)
    if not source:
        state.register_sources([citekey])
        source = state.get_source(citekey)

    entry = library.entries.get(citekey)
    if not entry:
        from .literature.models import ZoteroEntry
        entry = ZoteroEntry(id=citekey, title=citekey)

    if not quiet:
        console.print(f"[blue]Processing: {entry.authors_str} ({entry.year or '?'})[/blue] [dim]@{citekey}[/dim]")

    # Find PDF
    pdf_search_paths = [Path(cfg.zotero.storage_path)]
    pdf_path = pdf_extractor.find_pdf(
        citekey, pdf_search_paths,
        entry_title=entry.title or "",
        direct_path=source.get("pdf_path") if source else entry.pdf_path,
        pdf_lookup=library.pdf_paths,
    )

    if not pdf_path:
        if not quiet:
            console.print("  [red]PDF not found[/red]")
        return (0, "PDF not found")

    # Extract text
    pdf_text = pdf_extractor.extract(pdf_path)
    if not pdf_text or len(pdf_text) < cfg.processing.min_pdf_length:
        if not quiet:
            console.print("  [red]PDF extraction failed or text too short[/red]")
        return (0, "text too short")

    # Extract fragments
    result = extract_fragments(
        entry, pdf_text, cfg, state, ai,
        dissertation_context=dissertation_context,
        available_tags=available_tags,
        klemma_home=klemma_home,
    )

    if not result or not result.fragments:
        if not quiet:
            console.print("  [red]No fragments extracted[/red]")
        return (0, "no fragments")

    if not quiet:
        console.print(f"  [green]{len(result.fragments)} fragments[/green]", end="")

    # Save to vault
    saved_path = save_fragments_to_vault(
        citekey, result.fragments, vault,
        entry=entry, config=cfg, state=state,
        pdf_text=pdf_text, ai=ai, entry_lookup=library.entries,
        dissertation_context=dissertation_context,
        available_tags=available_tags,
        klemma_home=klemma_home,
    )
    if not quiet:
        if saved_path:
            console.print(f" → @{citekey}")
        else:
            console.print(" [dim](DB only)[/dim]")

    return (len(result.fragments), "ok")


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="Show full detailed tables")
@click.option("--chapter", "-ch", type=int, help="Filter by chapter")
@click.pass_context
def status(ctx, verbose, chapter):
    """Unified status: processing, coverage, gaps, reference gaps."""
    config_path = ctx.obj["config_path"]
    kctx = _init_components(config_path)
    cfg, state = kctx.config, kctx.state
    project = kctx.project
    _sync_sections(kctx, quiet=True)

    proc_stats = state.get_stats()
    frag_stats = state.get_fragment_stats()
    cov = state.get_coverage_stats()

    # --- Processing summary ---
    completed = proc_stats.get("completed", 0)
    pending = proc_stats.get("pending", 0)
    failed = proc_stats.get("failed", 0)
    total = proc_stats.get("total", 0)
    parts = [f"[green]{completed} completed[/green]"]
    if pending:
        parts.append(f"[yellow]{pending} pending[/yellow]")
    if failed:
        parts.append(f"[red]{failed} failed[/red]")
    console.print(f"Processing: {' | '.join(parts)}  [dim]({total} total, {frag_stats.get('total', 0)} fragments)[/dim]")
    console.print()

    # --- Coverage by chapter (dynamic from project config) ---
    chapter_numbers = project.chapter_numbers if project else list(range(1, 5))
    table = Table(title="Coverage by Chapter", show_edge=False, pad_edge=False)
    table.add_column("Chapter", style="cyan")
    table.add_column("Sources", justify="right", width=8)
    for ch in chapter_numbers:
        if chapter and ch != chapter:
            continue
        count = cov["chapters"].get(ch, 0)
        style = "green" if count >= 10 else "yellow" if count >= 5 else "red"
        name = (project.chapters.get(ch, "") if project
                else cfg.dissertation.chapters.get(ch, ""))
        table.add_row(f"Ch {ch}: {name}", f"[{style}]{count}[/{style}]")
    console.print(table)

    # --- Sections (verbose or filtered by chapter) ---
    if (verbose or chapter) and cov["sections"]:
        console.print()
        sec_table = Table(title="Coverage by Section", show_edge=False, pad_edge=False)
        sec_table.add_column("Section", style="cyan")
        sec_table.add_column("Sources", justify="right", width=8)
        for sec, count in sorted(cov["sections"].items()):
            if chapter and not sec.startswith(f"{chapter}."):
                continue
            style = "green" if count >= 3 else "yellow" if count >= 1 else "red"
            sec_table.add_row(sec, f"[{style}]{count}[/{style}]")
        console.print(sec_table)

    # --- Top gaps ---
    min_sources = (project.min_sources_per_section if project
                   else cfg.dissertation.min_sources_per_section)
    gaps_data = state.get_gaps(min_sources=min_sources)
    if gaps_data:
        if chapter:
            gaps_data = [g for g in gaps_data if g["section"].startswith(f"{chapter}.")]
        shown = gaps_data if verbose else gaps_data[:5]
        console.print()
        console.print(f"[bold]Top Gaps[/bold] [dim](sections with < {min_sources} sources)[/dim]")
        for gap in shown:
            needed = min_sources - gap["count"]
            console.print(f"  [red]{gap['section']}[/red] — {gap['count']} sources [dim](need {needed} more)[/dim]")
        if not verbose and len(gaps_data) > 5:
            console.print(f"  [dim]... and {len(gaps_data) - 5} more (use --verbose)[/dim]")

    # --- Reference gaps ---
    if verbose:
        _print_ref_gaps_table(state, limit=20)
    else:
        ref_gaps = state.get_reference_gaps(limit=5)
        if ref_gaps:
            gap_summary = state.get_gap_summary()
            console.print()
            console.print(f"[bold]Ref Gaps[/bold] [dim]({gap_summary['open_count']} open)[/dim]")
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


# Backward-compatible aliases
@main.command(hidden=True)
@click.pass_context
def stats(ctx):
    """[alias] → status"""
    ctx.invoke(status)


@main.command(hidden=True)
@click.pass_context
def coverage(ctx):
    """[alias] → status --verbose"""
    ctx.invoke(status, verbose=True)


@main.command(hidden=True)
@click.option("--min-sources", "-m", type=int, default=3)
@click.pass_context
def gaps(ctx, min_sources):
    """[alias] → status --verbose"""
    ctx.invoke(status, verbose=True)


@main.command()
@click.option("--section", "-s", required=True, help="Идентификатор раздела, например 1.3.2")
@click.option("--no-save", is_flag=True, help="Не сохранять в vault")
@click.option("--force", is_flag=True, help="Переизвлечь фрагменты даже если уже есть")
@click.option("--enrich", is_flag=True, help="Enrich with external search via MCP (requires academia server)")
@click.pass_context
def research(ctx, section, no_save, force, enrich):
    """Deep section analysis — argument structure, citation plan, gaps.

    Auto-processes unextracted sources before analysis.
    Use --force to re-extract all fragments.
    Use --enrich to add external paper search results to the analysis context.

    Example: klemma research --section 1.3.2
    """
    config_path = ctx.obj["config_path"]
    kctx = _init_components(config_path)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    project = kctx.project
    _sync_sections(kctx)
    ai = _init_ai(cfg)

    from .skills.researcher import pre_extract_sources, research_section

    chapter = int(section.split(".")[0])

    # Optional: enrich with external search via MCP
    enrichment_context = ""
    if enrich and kctx.tools and kctx.tools.has("academia"):
        chapter_name = (project.chapters.get(chapter, "") if project
                        else cfg.dissertation.chapters.get(chapter, ""))
        search_query = f"{chapter_name} {section}"
        with console.status(f"Searching arXiv for section {section}", spinner="dots2"):
            result = kctx.tools.call("academia", "arxiv_search", {"query": search_query, "limit": 5})
        if not result.is_error and result.content:
            enrichment_context = f"\n\n## External Search Results (ArXiv)\n{result.content}"  # noqa: F841
            console.print("[green]Found external papers for context[/green]")
        else:
            console.print("[yellow]External search returned no results[/yellow]")
    elif enrich:
        console.print("[yellow]--enrich requires academia MCP server (klemma tools add academia ...)[/yellow]")

    # Auto-process unextracted sources
    with console.status(f"Auto-processing unextracted sources for section {section}", spinner="arc"):
        extract_result = pre_extract_sources(
            section, chapter, cfg, state, vault, ai,
            force=force,
            library=kctx.library,
            on_progress=lambda ck, st, i, n: None,  # suppress inside spinner
            dissertation_context=kctx.dissertation_context,
            available_tags=kctx.available_tags,
            klemma_home=kctx.klemma_home,
        )

    extracted = extract_result["extracted"]
    skipped = extract_result["skipped"]
    no_pdf = extract_result["no_pdf"]
    if extracted > 0:
        console.print(f"[green]Processed {extracted} sources[/green]", end="")
        if skipped:
            console.print(f" [dim]({skipped} already cached)[/dim]")
        else:
            console.print()
    elif skipped > 0:
        console.print(f"[dim]All {skipped} sources already processed[/dim]")
    if no_pdf:
        for ck in no_pdf:
            console.print(f"  [yellow]@{ck}: PDF not found, skipping[/yellow]")

    # Проверить: первый запуск или обновление
    from .skills.researcher import _load_previous_research
    prev = _load_previous_research(section, chapter, state, vault)
    if prev:
        mode_label = "Инкрементальное обновление"
        details = []
        if prev["user_notes"]:
            details.append("заметки пользователя")
        details.append(f"пред. фрагментов: {prev['previous_fragment_count']}")
        spinner_text = f"{mode_label} раздела {section} ({', '.join(details)})"
    else:
        spinner_text = f"Анализ раздела {section}"

    with console.status(spinner_text, spinner="dots"):
        result = research_section(
            section, cfg, state, vault, ai, save_to_vault=not no_save,
            project=kctx.project,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
        )

    if not result.section_status:
        console.print("[red]Не удалось сгенерировать брифинг.[/red]")
        return

    console.print()

    # Статус раздела
    status_color = {
        "не начат": "red",
        "черновик": "yellow",
        "требует доработки": "yellow",
        "почти готов": "green",
        "готов": "green",
    }.get(result.section_status, "white")

    status_text = (
        f"[bold]{result.section_title or f'Раздел {section}'}[/bold]\n\n"
        f"Статус: [{status_color}]{result.section_status}[/{status_color}]\n"
        f"Объём: {result.current_word_count}/{result.target_word_count} слов "
        f"({result.readiness_pct}%)\n"
        f"Источников: {result.available_sources} | "
        f"Фрагментов: {result.available_fragments}"
    )
    console.print(Panel(status_text, title=f"Раздел {section}", border_style="blue"))

    # Распределение фрагментов
    if result.fragment_distribution:
        parts = [f"{t}: {c}" for t, c in result.fragment_distribution.items() if c > 0]
        if parts:
            console.print(f"\n[dim]Фрагменты: {', '.join(parts)}[/dim]")

    # Структура аргументации
    if result.argument_blocks:
        console.print()
        table = Table(title="Структура аргументации")
        table.add_column("#", justify="right", width=3, style="dim")
        table.add_column("Блок", max_width=30)
        table.add_column("Описание", max_width=45)
        table.add_column("Источники", max_width=25, style="cyan")
        table.add_column("Слов", justify="right", width=5)

        for block in result.argument_blocks:
            cites = ", ".join(f"@{c}" for c in block.citations[:3])
            if len(block.citations) > 3:
                cites += f" +{len(block.citations) - 3}"
            table.add_row(
                str(block.order),
                block.title,
                block.description[:45] + ("..." if len(block.description) > 45 else ""),
                cites,
                str(block.estimated_words),
            )
        console.print(table)

    # План цитирования
    if result.citation_plan:
        console.print()
        table = Table(title="План цитирования")
        table.add_column("Источник", width=25, style="cyan")
        table.add_column("Тип", width=12)
        table.add_column("Рел", justify="right", width=3)
        table.add_column("Где", max_width=35)
        table.add_column("Фрагмент", max_width=40, style="dim")

        for c in result.citation_plan:
            rel_style = "green" if c.relevance >= 4 else "yellow" if c.relevance >= 3 else "dim"
            table.add_row(
                f"@{c.citekey}",
                c.usage,
                f"[{rel_style}]{c.relevance}[/{rel_style}]",
                c.position[:35] if c.position else "",
                c.fragment_text[:40] + ("..." if len(c.fragment_text) > 40 else "") if c.fragment_text else "",
            )
        console.print(table)

    # Пробелы
    if result.missing_coverage:
        console.print("\n[yellow]Пробелы в покрытии:[/yellow]")
        for m in result.missing_coverage:
            console.print(f"  - {m}")

    # Рекомендации
    if result.writing_suggestions:
        console.print("\n[green]Рекомендации по написанию:[/green]")
        for s in result.writing_suggestions:
            console.print(f"  - {s}")

    # Сохранение
    if not no_save:
        console.print(f"\n[dim]Брифинг сохранён: Research_{section}.md[/dim]")


@main.command(name="import", hidden=True)
@click.option("--with-queue", is_flag=True, help="Also populate reading queue from high-priority sources")
@click.pass_context
def import_vault(ctx, with_queue):
    """Import/sync vault notes into klemma database.

    Scans @*.md files in the vault's notes folder, reads YAML frontmatter,
    and syncs source metadata and section assignments with the database.
    """
    config_path = ctx.obj["config_path"]
    kctx = _init_components(config_path)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    project = kctx.project

    result = _sync_sections(kctx, quiet=True)

    console.print(
        f"\n[green]Synced: {result['vault_updated']} updated, "
        f"{result['new_registered']} new, "
        f"{result['unchanged']} unchanged[/green]"
    )

    # Coverage summary
    cov = state.get_coverage_stats()
    chapters = cov.get("chapters", {})
    if chapters:
        console.print()
        table = Table(title="Coverage by Chapter")
        table.add_column("Chapter", style="cyan")
        table.add_column("Sources", justify="right")
        for ch in sorted(chapters):
            name = (project.chapters.get(ch, "") if project
                    else cfg.dissertation.chapters.get(ch, ""))
            table.add_row(f"Ch {ch}: {name}", str(chapters[ch]))
        console.print(table)

    # Reading queue from high-priority sources
    if with_queue:
        notes_folder = cfg.obsidian.notes_folder
        note_names = vault.list_notes(notes_folder)
        queue_added = 0
        for note_name in note_names:
            if not note_name.startswith("@"):
                continue
            props = vault.get_properties(note_name)
            if props and props.get("priority") == "high":
                citekey = note_name.lstrip("@")
                state.add_to_reading_queue(citekey, priority=80)
                queue_added += 1
        if queue_added:
            console.print(f"[blue]Reading queue: {queue_added} high-priority papers added.[/blue]")


@main.command()
@click.argument("query")
@click.option("--section", "-s", help="Focus on a specific section")
@click.option("--chapter", "-ch", type=int, help="Focus on a specific chapter")
@click.pass_context
def ask(ctx, query, section, chapter):
    """Ask a research question with full dissertation context.

    Example: klemma ask "What are the main ice forecast validation methods?"
    """
    config_path = ctx.obj["config_path"]
    kctx = _init_components(config_path)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    ai = _init_ai(cfg)

    from .skills.agent import build_agent_context

    with console.status("Сборка контекста исследования", spinner="dots"):
        context = build_agent_context(
            cfg, state, vault, section=section, chapter=chapter,
            project=kctx.project,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
        )

    console.print(f"[dim]Query: {query}[/dim]")

    if ai.interactive_available:
        import subprocess

        subprocess.run(["claude", "--system-prompt", context, query])
    else:
        with console.status("Генерация ответа", spinner="dots"):
            response = ai.call(system=context, user=query, max_tokens=8192)
        if response:
            console.print(response)
        else:
            console.print("[red]Не удалось получить ответ.[/red]")

    console.print("\n[dim]Сессия агента завершена.[/dim]")


@main.group(invoke_without_command=True)
@click.option("--section", "-s", help="Focus on a specific section (recommend mode)")
@click.option("--audit", is_flag=True, help="Deep quality audit")
@click.pass_context
def library(ctx, section, audit):
    """AI-powered library analysis and recommendations.

    Without flags: overall health assessment.
    With --section: reading recommendations for that section.
    With --audit: deep quality audit.
    """
    if ctx.invoked_subcommand is not None:
        return

    config_path = ctx.obj["config_path"]
    kctx = _init_components(config_path)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    _sync_sections(kctx)
    ai = _init_ai(cfg)

    from .skills.librarian import analyze_library

    entry_lookup = kctx.library.entries

    mode = "audit" if audit else "recommend" if section else "status"

    with console.status(f"Analyzing library ({mode})", spinner="dots"):
        report = analyze_library(
            cfg, state, vault, ai, entry_lookup, mode=mode, focus_section=section,
            project=kctx.project,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
        )

    if not report:
        console.print("[red]Failed to generate library analysis.[/red]")
        return

    # Overall health
    if report.overall_health:
        console.print(Panel(report.overall_health, title="Library Health", border_style="blue"))

    # Chapter assessments
    if report.chapter_assessments:
        table = Table(title="Chapter Assessment", show_edge=False, pad_edge=False)
        table.add_column("Ch", width=4, style="cyan")
        table.add_column("Sources", justify="right", width=8)
        table.add_column("Quality", justify="right", width=8)
        table.add_column("Verdict", max_width=50)
        for ch in report.chapter_assessments:
            table.add_row(
                str(ch.get("chapter", "?")),
                str(ch.get("sources", "?")),
                str(ch.get("quality_avg", "?")),
                ch.get("verdict", "")[:50],
            )
        console.print(table)

    # Critical issues
    if report.critical_issues:
        console.print("\n[bold red]Critical Issues[/bold red]")
        for issue in report.critical_issues:
            console.print(f"  [red]-[/red] {issue}")

    # Recommendations
    if report.recommendations:
        console.print("\n[bold green]Recommendations[/bold green]")
        for rec in report.recommendations:
            priority = rec.get("priority", "medium")
            style = {"high": "red", "medium": "yellow", "low": "dim"}.get(priority, "white")
            console.print(f"  [{style}]{priority.upper()}[/{style}] {rec.get('action', '')}")
            if rec.get("reason"):
                console.print(f"        [dim]{rec['reason']}[/dim]")

    # Section detail (recommend mode)
    if report.section_detail:
        detail = report.section_detail
        if detail.get("current_sources_assessment"):
            console.print(f"\n[bold]Section Assessment[/bold]\n{detail['current_sources_assessment']}")
        if detail.get("reading_order"):
            console.print("\n[bold]Reading Order[/bold]")
            for i, item in enumerate(detail["reading_order"], 1):
                console.print(f"  {i}. {item.get('citekey_or_ref', '?')} — {item.get('reason', '')}")

    # Audit findings
    if report.audit_findings:
        console.print("\n[bold]Audit Findings[/bold]")
        for finding in report.audit_findings:
            severity = finding.get("severity", "medium")
            style = {"high": "red", "medium": "yellow", "low": "dim"}.get(severity, "white")
            console.print(f"  [{style}]{severity.upper()}[/{style}] [{finding.get('type', '')}] {finding.get('details', '')}")

    # Prune recommendations (auto-triggered when >100 sources)
    if report.prune:
        prune = report.prune
        drop = prune.get("drop", [])
        maybe = prune.get("maybe", [])
        total = state.get_library_summary().get("total", 0)
        after = total - len(drop)
        src_lookup = {s["id"]: s for s in state.get_all_sources()}

        console.print(f"\n[bold yellow]Prune Analysis[/bold yellow] [dim]({total} → ~{after} sources)[/dim]")

        def _prune_table(items: list[dict], title: str, style: str) -> Table:
            t = Table(title=f"{title} ({len(items)})", show_edge=False, pad_edge=False)
            t.add_column("#", width=4, style="dim")
            t.add_column("Citekey", max_width=35, style=style)
            t.add_column("Q", width=3, justify="right")
            t.add_column("F", width=3, justify="right")
            t.add_column("Reason", max_width=50)
            for i, item in enumerate(items, 1):
                ck = item.get("citekey", "?").lstrip("@")
                src = src_lookup.get(ck, {})
                t.add_row(
                    str(i),
                    f"@{ck}",
                    str(src.get("quality_score") or "?"),
                    str(src.get("fragment_count") or "?"),
                    item.get("reason", ""),
                )
            return t

        if drop:
            console.print(_prune_table(drop, "Drop", "red"))
        if maybe:
            console.print(_prune_table(maybe, "Maybe", "yellow"))

    # Reference gaps table
    _print_ref_gaps_table(state)

    console.print("\n[dim]Full report saved to vault.[/dim]")


@library.command()
@click.option("-c", "--chapter", type=int, help="Filter by chapter number")
@click.option("-v", "--verdict", type=click.Choice(["drop", "maybe"]), help="Filter by verdict")
@click.option("--clear", "clear_key", help="Clear verdict for a citekey")
@click.pass_context
def prune(ctx, chapter, verdict, clear_key):
    """Browse and manage prune verdicts from library analysis."""
    config_path = ctx.obj["config_path"]
    kctx = _init_components(config_path)
    state = kctx.state

    if clear_key and (chapter is not None or verdict is not None):
        console.print("[red]--clear cannot be combined with -c/-v[/red]")
        return

    if clear_key:
        key = clear_key.lstrip("@")
        state.clear_prune_verdict(key)
        console.print(f"[green]Cleared prune verdict for @{key}[/green]")
        return

    items = state.get_prune_verdicts(chapter=chapter, verdict=verdict)
    if not items:
        label = []
        if verdict:
            label.append(f"verdict={verdict}")
        if chapter is not None:
            label.append(f"chapter={chapter}")
        hint = f" ({', '.join(label)})" if label else ""
        console.print(f"[dim]No prune verdicts found{hint}.[/dim]")
        console.print("[dim]Run 'klemma library' to generate verdicts.[/dim]")
        return

    table = Table(
        title=f"Prune Verdicts ({len(items)})",
        show_edge=False, pad_edge=False,
    )
    table.add_column("#", width=4, style="dim")
    table.add_column("Verdict", width=7)
    table.add_column("Citekey", max_width=35)
    table.add_column("Q", width=3, justify="right")
    table.add_column("F", width=3, justify="right")
    table.add_column("Sections", max_width=15, style="dim")
    table.add_column("Reason", max_width=45)

    for i, item in enumerate(items, 1):
        v = item["verdict"]
        v_style = "red" if v == "drop" else "yellow"
        table.add_row(
            str(i),
            f"[{v_style}]{v}[/{v_style}]",
            f"@{item['source_id']}",
            str(item.get("quality_score") or "?"),
            str(item.get("fragment_count") or "?"),
            item.get("sections") or "",
            item.get("reason") or "",
        )

    console.print(table)
    summary = state.get_prune_summary()
    console.print(f"\n[dim]Total: {summary['drop']} drop, {summary['maybe']} maybe[/dim]")


# --- Acquire: download + add to Zotero + register ---

@main.command()
@click.argument("url", required=False)
@click.option("--title", "-t", help="Paper title")
@click.option("--authors", "-a", help="Authors (comma-separated)")
@click.option("--year", "-y", type=int, help="Publication year")
@click.option("--journal", "-j", help="Journal name")
@click.option("--volume", help="Volume")
@click.option("--issue", help="Issue")
@click.option("--section", "-s", multiple=True, help="Dissertation section(s) to assign")
@click.option("--batch", "batch_path", type=click.Path(exists=True), help="JSON file with papers list")
@click.option("--no-process", is_flag=True, help="Skip fragment extraction after adding")
@click.pass_context
def acquire(ctx, url, title, authors, year, journal, volume, issue, section, batch_path, no_process):
    """Download PDF, add to Zotero, register in klemma.

    Single paper: klemma acquire <pdf_url> --title "..." --authors "..." --year 2022 --section 1.2
    Batch: klemma acquire --batch papers.json
    """
    from .literature.zotero import ZoteroLibrary
    from .skills.acquirer import PaperMetadata, acquire_paper, load_batch

    config_path = ctx.obj["config_path"]
    kctx = _init_components(config_path)
    cfg, state = kctx.config, kctx.state

    # Validate Zotero config
    if not cfg.zotero.library_id:
        console.print("[red]zotero.library_id not set in config.yaml[/red]")
        return
    api_key = cfg.zotero.api_key
    if not api_key:
        console.print(f"[red]{cfg.zotero.api_key_env} not set in environment[/red]")
        return

    zot_lib = ZoteroLibrary(
        library_id=cfg.zotero.library_id,
        library_type=cfg.zotero.library_type,
        api_key=api_key,
    )

    # Build paper list
    if batch_path:
        papers = load_batch(batch_path)
        console.print(f"[blue]Loaded {len(papers)} papers from batch file[/blue]")
    elif url:
        papers = [PaperMetadata(
            url=url,
            title=title or "",
            authors=authors or "",
            year=year,
            journal=journal or "",
            volume=volume or "",
            issue=issue or "",
            sections=list(section),
        )]
    else:
        console.print("[red]Provide a URL or --batch file[/red]")
        return

    library_json = cfg.zotero.library_json or ""
    ok = 0

    for i, meta in enumerate(papers, 1):
        label = meta.title[:50] if meta.title else meta.url[:50]
        console.print(f"\n[bold][{i}/{len(papers)}] {label}[/bold]")

        result = acquire_paper(
            meta, zot_lib, library_json,
            storage_path=cfg.zotero.storage_path, state=state,
        )

        if result.status == "ok":
            console.print(f"  [green]@{result.citekey}[/green] (item: {result.item_key})")
            if meta.sections:
                console.print(f"  [dim]sections: {', '.join(meta.sections)}[/dim]")

            if not no_process:
                try:
                    ai = _init_ai(cfg)
                except Exception as e:
                    console.print(f"  [yellow]Skipping auto-process (AI unavailable: {e})[/yellow]")
                    console.print(f"  [dim]Run manually: klemma process {result.citekey}[/dim]")
                    ai = None

                if ai:
                    from .literature.pdf import PDFExtractor
                    pdf_extractor = PDFExtractor(max_chars=cfg.ai.max_pdf_chars)
                    with console.status(f"Extracting fragments from @{result.citekey}", spinner="arc"):
                        _process_single(result.citekey, cfg, state, kctx.vault, ai, pdf_extractor, kctx.library,
                                        dissertation_context=kctx.dissertation_context,
                                        available_tags=kctx.available_tags,
                                        klemma_home=kctx.klemma_home)

            ok += 1
        else:
            console.print(f"  [red]{result.status}[/red]")

    console.print(f"\n[green]Done: {ok}/{len(papers)} acquired.[/green]")


# --- Search: external paper search via MCP ---

@main.command()
@click.argument("query")
@click.option("--source", "-src", default="arxiv", type=click.Choice(["arxiv", "s2"]), help="Search source")
@click.option("--limit", "-n", default=10, help="Max results")
@click.pass_context
def search(ctx, query, source, limit):
    """Search for papers via external MCP servers.

    Requires academia MCP server: klemma tools add academia --command python3 --args "-m" --args "academia_mcp"

    Example: klemma search "AMSR2 sea ice forecast validation"
    """
    config_path = ctx.obj["config_path"]
    kctx = _init_components(config_path)
    cfg = kctx.config

    if "academia" not in cfg.mcp.servers:
        console.print("[red]Academia MCP server not configured.[/red]")
        console.print(
            '[dim]Add it with: klemma tools add academia '
            '--command python3 --args "-m" --args academia_mcp[/dim]'
        )
        return

    registry = ToolRegistry(cfg)
    tool_name = "arxiv_search" if source == "arxiv" else "s2_search"

    with console.status(f"Searching {source}: {query}", spinner="dots2"):
        result = registry.call("academia", tool_name, {"query": query, "limit": limit})

    if result.is_error:
        console.print(f"[red]Search failed: {result.content}[/red]")
        return

    console.print(result.content)


# --- Discover: background literature discovery ---

@main.command()
@click.option("--section", "-s", help="Section to discover papers for")
@click.option("--status", "show_status", is_flag=True, help="Show discovery status")
@click.option("--review", is_flag=True, help="Review pending discoveries")
@click.option("--background", "-bg", is_flag=True, help="Run in background")
@click.pass_context
def discover(ctx, section, show_status, review, background):
    """Discover new literature via external search.

    Requires academia MCP server.

    \b
    Examples:
      klemma discover -s 1.3.2           # search and assess
      klemma discover -s 1.3.2 -bg       # run in background
      klemma discover --status            # show all discoveries
      klemma discover --review            # review pending results
    """
    config_path = ctx.obj["config_path"]
    kctx = _init_components(config_path)
    cfg, state = kctx.config, kctx.state

    if show_status:
        _discover_status(state)
        return

    if review:
        _discover_review(state)
        return

    if not section:
        console.print("[red]--section required for discovery. Example: klemma discover -s 1.3.2[/red]")
        return

    if "academia" not in cfg.mcp.servers:
        console.print("[red]Academia MCP server not configured.[/red]")
        console.print(
            '[dim]Add it with: klemma tools add academia '
            '--command python3 --args "-m" --args academia_mcp[/dim]'
        )
        return

    # Resolve config_path for subprocess/discovery
    effective_config = config_path
    if not effective_config and kctx.project_root:
        effective_config = str(kctx.project_root / ".klemma" / "config.yaml")

    if background:
        import subprocess as sp

        log_path = (kctx.project_root or Path.home()) / f".klemma/discovery_{section}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        sp.Popen(
            [sys.executable, "-m", "klemma.tools.discovery",
             "--section", section, "--config", effective_config],
            stdout=open(log_path, "w"),
            stderr=sp.STDOUT,
        )
        console.print(f"[green]Discovery started in background for section {section}[/green]")
        console.print(f"[dim]Log: {log_path}[/dim]")
        console.print("[dim]Review results: klemma discover --review[/dim]")
        return

    from .tools.discovery import run_discovery

    with console.status(f"Discovering papers for section {section}", spinner="earth"):
        result = run_discovery(section=section, config_path=effective_config)

    console.print(
        f"[green]Done:[/green] searched {result['searched']}, "
        f"found {result['found']} new, assessed {result['assessed']}"
    )
    if result["errors"]:
        for err in result["errors"]:
            console.print(f"[red]  {err}[/red]")

    if result["found"] > 0:
        console.print("\n[dim]Review results: klemma discover --review[/dim]")


def _discover_status(state):
    """Show discovery status across all sections."""
    for status_type in ("pending", "assessed", "accepted", "rejected"):
        discoveries = state.get_discoveries(status=status_type, limit=100)
        if not discoveries:
            continue

        sections = {}
        for d in discoveries:
            sec = d["section"]
            sections[sec] = sections.get(sec, 0) + 1

        label = {"pending": "yellow", "assessed": "blue", "accepted": "green", "rejected": "dim"}
        style = label.get(status_type, "white")
        console.print(f"[{style}]{status_type.title()}:[/{style}] ", end="")
        parts = [f"{sec} ({cnt})" for sec, cnt in sorted(sections.items())]
        console.print(", ".join(parts))

    if not any(state.get_discoveries(status=s) for s in ("pending", "assessed", "accepted", "rejected")):
        console.print("[dim]No discoveries yet. Run: klemma discover -s <section>[/dim]")


def _discover_review(state):
    """Interactive review of pending/assessed discoveries."""
    discoveries = state.get_discoveries(status="pending", limit=50)
    assessed = state.get_discoveries(status="assessed", limit=50)
    all_pending = discoveries + assessed

    if not all_pending:
        console.print("[dim]No discoveries to review.[/dim]")
        return

    table = Table(title=f"Discoveries to Review ({len(all_pending)})", show_edge=False, pad_edge=False)
    table.add_column("ID", width=4, style="dim")
    table.add_column("Sec", width=6, style="cyan")
    table.add_column("Rel", width=3, justify="right")
    table.add_column("Year", width=5)
    table.add_column("Authors", width=20)
    table.add_column("Title", max_width=40)
    table.add_column("Use", width=10, style="dim")

    for d in all_pending:
        rel = d.get("relevance_score")
        rel_str = str(rel) if rel else "?"
        rel_style = "green" if rel and rel >= 4 else "yellow" if rel and rel >= 3 else "dim"
        table.add_row(
            str(d["id"]),
            d.get("section", ""),
            f"[{rel_style}]{rel_str}[/{rel_style}]",
            str(d.get("year") or ""),
            (d.get("authors") or "")[:20],
            (d.get("title") or "")[:40],
            d.get("usage_type") or "",
        )

    console.print(table)
    console.print("\n[dim]Accept/reject via: klemma discover --review (interactive coming soon)[/dim]")


# --- Tools: MCP server management ---

@main.group()
def tools():
    """Manage MCP tool servers (add, list, remove, call)."""
    pass


@tools.command(name="add")
@click.argument("name")
@click.option("--command", "-cmd", required=True, help="Command to launch server (e.g. uvx, python3)")
@click.option("--args", "-a", multiple=True, help="Arguments (repeatable)")
@click.option("--env", "-e", multiple=True, help="Environment vars as KEY=VALUE (repeatable)")
@click.option("--global", "is_global", is_flag=True, help="Add to global ~/.klemma/ config")
@click.pass_context
def tools_add(ctx, name, command, args, env, is_global):
    """Register an MCP server.

    Example: klemma tools add zotero --command uvx --args zotero-mcp --env ZOTERO_LOCAL=true
    """
    from .tools.registry import add_server

    env_dict = {}
    for item in env:
        if "=" in item:
            k, v = item.split("=", 1)
            env_dict[k] = v
        else:
            console.print(f"[red]Invalid env format: {item} (use KEY=VALUE)[/red]")
            return

    if is_global:
        config_path = str(ensure_system_home() / "config.yaml")
    elif ctx.obj["config_path"]:
        config_path = ctx.obj["config_path"]
    else:
        project_root = discover_project_root()
        if project_root is None:
            console.print("[red]Not in a klemma project. Use --global or 'klemma init' first.[/red]")
            return
        config_path = str(project_root / ".klemma" / "config.yaml")

    add_server(config_path, name, command, list(args), env_dict)
    scope = "global" if is_global else "project"
    console.print(f"[green]Added MCP server '{name}' ({scope})[/green]")
    console.print(f"  command: {command} {' '.join(args)}")
    if env_dict:
        console.print(f"  env: {env_dict}")
    console.print("\n[dim]Verify with: klemma tools list[/dim]")


@tools.command(name="list")
@click.option("--probe", is_flag=True, help="Connect to each server and list available tools")
@click.pass_context
def tools_list(ctx, probe):
    """List registered MCP servers."""
    kctx = _init_components(ctx.obj["config_path"])
    cfg = kctx.config

    servers = cfg.mcp.servers
    if not servers:
        console.print("[dim]No MCP servers registered.[/dim]")
        console.print("[dim]Add one with: klemma tools add <name> --command <cmd> --args <arg>[/dim]")
        return

    table = Table(title="MCP Servers", show_edge=False, pad_edge=False)
    table.add_column("Name", style="cyan")
    table.add_column("Command", max_width=40)
    if probe:
        table.add_column("Tools", max_width=50)

    for name, srv in servers.items():
        cmd_str = f"{srv.command} {' '.join(srv.args)}"
        if probe:
            try:
                registry = ToolRegistry(cfg)
                tool_names = registry.available_tools(name)
                tools_str = f"[green]{len(tool_names)}[/green]: {', '.join(tool_names)}"
            except Exception as e:
                tools_str = f"[red]error: {e}[/red]"
            table.add_row(name, cmd_str, tools_str)
        else:
            table.add_row(name, cmd_str)

    console.print(table)


@tools.command(name="remove")
@click.argument("name")
@click.option("--global", "is_global", is_flag=True, help="Remove from global ~/.klemma/ config")
@click.pass_context
def tools_remove(ctx, name, is_global):
    """Remove an MCP server registration."""
    from .tools.registry import remove_server

    if is_global:
        config_path = str(ensure_system_home() / "config.yaml")
    elif ctx.obj["config_path"]:
        config_path = ctx.obj["config_path"]
    else:
        project_root = discover_project_root()
        if project_root is None:
            console.print("[red]Not in a klemma project.[/red]")
            return
        config_path = str(project_root / ".klemma" / "config.yaml")

    if remove_server(config_path, name):
        console.print(f"[green]Removed MCP server '{name}'[/green]")
    else:
        console.print(f"[red]Server '{name}' not found[/red]")


@tools.command(name="call")
@click.argument("server")
@click.argument("tool")
@click.argument("args_json", default="{}")
@click.pass_context
def tools_call(ctx, server, tool, args_json):
    """Call a tool directly (debug/power user).

    Example: klemma tools call zotero zotero_search_items '{"query": "ice"}'
    """
    kctx = _init_components(ctx.obj["config_path"])
    cfg = kctx.config

    if server not in cfg.mcp.servers:
        console.print(f"[red]Server '{server}' not registered[/red]")
        return

    try:
        tool_args = json.loads(args_json)
    except json.JSONDecodeError as e:
        console.print(f"[red]Invalid JSON: {e}[/red]")
        return

    registry = ToolRegistry(cfg)
    with console.status(f"Calling {server}.{tool}", spinner="bounce"):
        result = registry.call(server, tool, tool_args)

    if result.is_error:
        console.print(f"[red]Error: {result.content}[/red]")
    else:
        console.print(result.content)


# --- Info & Tree: project introspection ---

@main.command()
@click.pass_context
def info(ctx):
    """Show current project info: root, parent chain, config, DB."""
    config_path = ctx.obj["config_path"]

    project_chain = discover_project_chain()
    if not project_chain and not config_path:
        console.print("[yellow]Not in a klemma project.[/yellow]")
        console.print("[dim]Run 'klemma init' to create a project here.[/dim]")
        return

    try:
        kctx = _init_components(config_path)
    except Exception as e:
        console.print(f"[red]Error loading project: {e}[/red]")
        return

    project = kctx.project
    project_root = kctx.project_root or Path.cwd()

    # Project info panel
    info_parts = [f"[bold]{project.title or 'Untitled'}[/bold]"]
    info_parts.append(f"Type: {project.type}")
    info_parts.append(f"Root: {project_root}")
    if project.current_focus:
        info_parts.append(f"Focus: {project.current_focus}")
    info_parts.append(f"Chapters: {len(project.chapters)}")
    info_parts.append(f"DB: {kctx.config.state.db_path}")
    if kctx.config.obsidian.vault_path:
        info_parts.append(f"Vault: {kctx.config.obsidian.vault_path}")

    console.print(Panel(
        "\n".join(info_parts),
        title=f"Project: {kctx.project_name}",
        border_style="blue",
    ))

    # Parent chain
    if len(kctx.project_chain) > 1:
        console.print("\n[bold]Project Chain[/bold] (child → parent):")
        for i, root in enumerate(kctx.project_chain):
            marker = "[green]●[/green]" if i == 0 else "[dim]○[/dim]"
            console.print(f"  {marker} {root.name} ({root})")

    # Chapter structure
    if project.chapters:
        table = Table(title="Structure", show_edge=False, pad_edge=False)
        table.add_column("Ch", width=4, style="cyan")
        table.add_column("Title")
        for ch_num in sorted(project.chapters.keys()):
            table.add_row(str(ch_num), project.chapters[ch_num])
        console.print(table)


@main.command()
@click.pass_context
def tree(ctx):
    """Show nested project tree from current root."""
    project_root = discover_project_root()
    if project_root is None:
        console.print("[yellow]Not in a klemma project.[/yellow]")
        return

    # Find topmost parent
    chain = discover_project_chain()
    top = chain[-1] if chain else project_root

    console.print(f"[bold]Project Tree[/bold] (from {top})\n")
    _print_project_tree(top, indent=0, current=project_root)


def _print_project_tree(root: Path, indent: int = 0, current: Path | None = None):
    """Recursively print project tree."""
    from .config import _load_yaml

    prefix = "  " * indent
    marker = "[green]●[/green]" if root == current else "[dim]○[/dim]"

    # Load project title from config
    config_raw = _load_yaml(root / ".klemma" / "config.yaml")
    project_raw = config_raw.get("project", {})
    title = project_raw.get("title", "") if isinstance(project_raw, dict) else ""
    ptype = project_raw.get("type", "") if isinstance(project_raw, dict) else ""

    label = f"{root.name}"
    if title:
        label += f" — {title}"
    if ptype:
        label += f" [{ptype}]"

    console.print(f"{prefix}{marker} {label}")

    # Scan subdirectories for child projects
    try:
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / ".klemma").is_dir() and child.name != ".klemma":
                _print_project_tree(child, indent + 1, current)
    except PermissionError:
        pass


# --- Migrate: convert old ~/.klemma/ to per-directory project ---

@main.command()
@click.option("--dry-run", is_flag=True, help="Preview changes without modifying anything")
@click.pass_context
def migrate(ctx, dry_run):
    """Migrate from ~/.klemma/ centralized config to per-directory project.

    Splits the old ~/.klemma/config.yaml into:
    - ~/.klemma/config.yaml (system: AI settings only)
    - .klemma/config.yaml (project: everything else)
    Also copies context.md → KLEMMA.md, tags.yaml, and DB.
    """
    import shutil

    import yaml as _yaml

    system_home = ensure_system_home()
    old_config_path = system_home / "config.yaml"

    if not old_config_path.exists():
        console.print(f"[yellow]No config found at {old_config_path}[/yellow]")
        return

    # Check if already in a project
    if discover_project_root() is not None:
        console.print("[yellow]Already in a klemma project (.klemma/ found).[/yellow]")
        console.print("[dim]Migration is for converting old ~/.klemma/ setups.[/dim]")
        return

    with open(old_config_path, "r", encoding="utf-8") as f:
        old_raw = _yaml.safe_load(f) or {}

    # Check this looks like a full project config (has obsidian: section)
    if "obsidian" not in old_raw:
        console.print("[dim]~/.klemma/config.yaml looks like a system config already (no obsidian: section).[/dim]")
        console.print("[dim]Just run 'klemma init' to create a project.[/dim]")
        return

    project_dir = Path.cwd()
    klemma_dir = project_dir / ".klemma"

    # Split config
    system_keys = {"ai"}
    system_raw = {k: v for k, v in old_raw.items() if k in system_keys}
    project_raw = {k: v for k, v in old_raw.items() if k not in system_keys}

    # Files to copy
    copies = []
    old_context = system_home / "context.md"
    if old_context.exists():
        copies.append((old_context, project_dir / "KLEMMA.md"))
    old_tags = system_home / "tags.yaml"
    if old_tags.exists():
        copies.append((old_tags, klemma_dir / "tags.yaml"))
    old_db = system_home / "data" / "klemma.db"
    if old_db.exists():
        copies.append((old_db, klemma_dir / "data" / "klemma.db"))
    old_prompts = system_home / "prompts"
    if old_prompts.is_dir() and any(old_prompts.iterdir()):
        copies.append((old_prompts, klemma_dir / "prompts"))

    if dry_run:
        console.print("[bold]Dry run — no changes will be made:[/bold]\n")
        console.print(f"  Rewrite {old_config_path} → system config (ai only)")
        console.print(f"  Create  {klemma_dir / 'config.yaml'} → project config")
        for src, dst in copies:
            console.print(f"  Copy    {src} → {dst}")
        return

    # Execute
    klemma_dir.mkdir(parents=True, exist_ok=True)
    (klemma_dir / "data").mkdir(exist_ok=True)

    # Write project config
    project_config_path = klemma_dir / "config.yaml"
    with open(project_config_path, "w", encoding="utf-8") as f:
        _yaml.dump(project_raw, f, default_flow_style=False, allow_unicode=True)
    console.print(f"  [green]+ {project_config_path}[/green]")

    # Rewrite system config
    with open(old_config_path, "w", encoding="utf-8") as f:
        f.write("# Klemma global config — AI defaults\n")
        _yaml.dump(system_raw, f, default_flow_style=False, allow_unicode=True)
    console.print(f"  [green]~ {old_config_path} (system only)[/green]")

    # Copy files
    for src, dst in copies:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        console.print(f"  [green]+ {dst}[/green]")

    # .gitignore
    gitignore = project_dir / ".gitignore"
    ignore_line = ".klemma/data/"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if ignore_line not in content:
            with open(gitignore, "a", encoding="utf-8") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(f"{ignore_line}\n")
    else:
        gitignore.write_text(f"# Klemma data\n{ignore_line}\n", encoding="utf-8")

    console.print(f"\n[green]Migration complete.[/green]")
    console.print(f"[dim]Project created in {project_dir}/[/dim]")
    console.print(f"[dim]System config at {system_home}/[/dim]")


# --- Backward-compatible aliases ---

@main.command(hidden=True)
@click.pass_context
def morning(ctx):
    """[alias] → plan"""
    ctx.invoke(plan)


@main.command(hidden=True)
@click.argument("citekey")
@click.pass_context
def extract(ctx, citekey):
    """[alias] → process"""
    ctx.invoke(process, citekey=citekey)


@main.command(hidden=True)
@click.argument("query")
@click.option("--section", "-s", default=None)
@click.option("--chapter", "-ch", type=int, default=None)
@click.pass_context
def agent(ctx, query, section, chapter):
    """[alias] → ask"""
    ctx.invoke(ask, query=query, section=section, chapter=chapter)


@main.command(hidden=True)
@click.option("--with-queue", is_flag=True)
@click.pass_context
def prepopulate(ctx, with_queue):
    """[alias] → import"""
    ctx.invoke(import_vault, with_queue=with_queue)


if __name__ == "__main__":
    main()
