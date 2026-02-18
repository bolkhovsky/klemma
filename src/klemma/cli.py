"""Klemma CLI — dual-mode: headless commands + TUI dashboard."""

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__, get_banner
from .ai import ClaudeClient
from .config import load_config
from .literature.pdf import PDFExtractor
from .state import StateManager
from .vault import VaultAdapter

console = Console()


def _init_components(config_path: str):
    """Initialize all components from config."""
    cfg = load_config(config_path)
    state = StateManager(cfg.state.db_path)
    vault = VaultAdapter(cfg.obsidian.vault_path, use_cli=cfg.obsidian.use_cli)
    return cfg, state, vault


def _init_ai(cfg):
    """Initialize AI client (separate to allow commands without API key)."""
    return ClaudeClient(cfg.ai)


def _sync_sections(cfg, state, vault, quiet=False) -> dict:
    """Sync section assignments from vault frontmatter + discover new Zotero entries.

    Fast (~60ms for 138 notes). Safe to call on every command.
    """
    from .literature.note_factory import auto_classify
    from .literature.pdf import PDFExtractor

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

    # 2. Discover new Zotero entries not in DB
    new_entries = []
    if cfg.zotero.library_json:
        entry_lookup = PDFExtractor.load_entry_lookup(Path(cfg.zotero.library_json))
        vault_citekeys = {vd["citekey"] for vd in vault_data}

        with state._conn() as conn:
            cur = conn.execute("SELECT id FROM sources")
            existing = {row["id"] for row in cur.fetchall()}

        for citekey, entry in entry_lookup.items():
            if citekey not in existing and citekey not in vault_citekeys:
                classification = auto_classify(entry, cfg)
                new_entries.append((citekey, classification))

    # 3. Sync to DB
    result = state.sync_source_sections(vault_data, new_entries)

    if not quiet:
        parts = []
        if result["vault_updated"]:
            parts.append(f"[green]{result['vault_updated']} updated from vault[/green]")
        if result["new_registered"]:
            parts.append(f"[blue]{result['new_registered']} new from Zotero[/blue]")
        if parts:
            console.print("[dim]Sync:[/dim] " + " | ".join(parts))

    return result


def _print_status_line(state: StateManager):
    """Print a compact status line with key metrics."""
    try:
        stats = state.get_stats()
        frag_stats = state.get_fragment_stats()
        parts = [
            f"[dim]{stats.get('total', 0)} sources[/dim]",
            f"[dim]{frag_stats.get('total', 0)} fragments[/dim]",
        ]
        gap_summary = state.get_gap_summary()
        if gap_summary["open_count"] > 0:
            top = ""
            if gap_summary["top_ref"]:
                top = f" (top: {gap_summary['top_ref']} x{gap_summary['top_count']})"
            parts.append(f"[yellow]{gap_summary['open_count']} ref-gaps{top}[/yellow]")
        console.print(f"[dim]|[/dim] " + " [dim]|[/dim] ".join(parts))
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
    ref_table.add_column("Title", max_width=35)
    ref_table.add_column("Why", max_width=30, style="dim")

    for i, g in enumerate(ref_gaps, 1):
        score_style = "red bold" if g["score"] >= 10 else "yellow" if g["score"] >= 5 else "dim"
        ref_table.add_row(
            str(i),
            f"[{score_style}]{g['score']:.1f}[/{score_style}]",
            str(g["count"]),
            (g["ref_authors"] or "")[:20],
            str(g.get("ref_year") or ""),
            (g["ref_title"] or "")[:35],
            (g.get("why_relevant") or "")[:30],
        )
    console.print()
    console.print(ref_table)


@click.group(invoke_without_command=True)
@click.version_option(version=__version__)
@click.option("--config", "-c", default="config.yaml", help="Config file path")
@click.pass_context
def main(ctx, config):
    """Klemma — AI academic assistant.

    Run without arguments to launch TUI dashboard.
    """
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config

    # Banner
    console.print(get_banner(cwd=str(Path.cwd())))

    if ctx.invoked_subcommand is not None:
        # Print status line for CLI subcommands
        try:
            cfg, state, _ = _init_components(config)
            _print_status_line(state)
        except Exception:
            pass

    if ctx.invoked_subcommand is None:
        # No subcommand → launch TUI
        try:
            from .app import KlemmaApp
            cfg, state, vault = _init_components(config)
            app = KlemmaApp(cfg=cfg, state=state, vault=vault)
            app.run()
        except ImportError as e:
            console.print(f"[red]TUI not available: {e}[/red]")
            console.print("Install textual: pip install textual")
            sys.exit(1)
        except Exception as e:
            console.print(f"[red]Error launching TUI: {e}[/red]")
            sys.exit(1)


@main.command()
@click.pass_context
def plan(ctx):
    """Daily plan — focus, recommendations, deadlines."""
    config_path = ctx.obj["config_path"]
    cfg, state, vault = _init_components(config_path)
    ai = _init_ai(cfg)

    from .skills.planner import generate_morning_plan

    console.print("[blue]Генерация утреннего брифинга...[/blue]")

    plan = generate_morning_plan(cfg, state, vault, ai)

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
@click.argument("citekey", required=False)
@click.pass_context
def process(ctx, citekey):
    """Process source(s): extract fragments, annotate, create vault note.

    With CITEKEY: process a single source.
    Without arguments: process all pending sources (up to 10).
    """
    config_path = ctx.obj["config_path"]
    cfg, state, vault = _init_components(config_path)
    ai = _init_ai(cfg)

    from .literature.pdf import PDFExtractor

    pdf_extractor = PDFExtractor(max_chars=cfg.ai.max_pdf_chars)

    # Load entry lookup for rich metadata and PDF paths
    entry_lookup = PDFExtractor.load_entry_lookup(Path(cfg.zotero.library_json)) if cfg.zotero.library_json else {}

    # Auto-resolve previously detected reference gaps against current library
    resolved = state.resolve_gaps(entry_lookup)
    if resolved:
        console.print(f"[green]Auto-resolved {resolved} reference gap(s)[/green]")

    # Build citekey list: single or batch
    if citekey:
        citekeys = [citekey]
    else:
        # Process all pending sources (up to 10)
        proc_stats = state.get_stats()
        if proc_stats.get("pending", 0) == 0:
            console.print("[green]No pending sources to process.[/green]")
            return
        citekeys = state.get_pending_sources(limit=10)
        console.print(f"[blue]Processing {len(citekeys)} pending sources...[/blue]")

    processed = 0
    for idx, ck in enumerate(citekeys, 1):
        if len(citekeys) > 1:
            console.print(f"\n[bold][{idx}/{len(citekeys)}] {ck}[/bold]")

        _process_single(ck, cfg, state, vault, ai, pdf_extractor, entry_lookup)
        processed += 1

    if len(citekeys) > 1:
        console.print(f"\n[green]Done: {processed}/{len(citekeys)} processed.[/green]")


def _process_single(citekey, cfg, state, vault, ai, pdf_extractor, entry_lookup):
    """Process a single source: find PDF, extract fragments, save to vault."""
    from .skills.extractor import extract_fragments, save_fragments_to_vault

    source = state.get_source(citekey)
    if not source:
        state.register_sources([citekey])
        source = state.get_source(citekey)

    entry = entry_lookup.get(citekey)
    if not entry:
        from .literature.models import ZoteroEntry
        entry = ZoteroEntry(id=citekey, title=citekey)

    console.print(f"[blue]Processing: {entry.authors_str} ({entry.year or '?'})[/blue] [dim]@{citekey}[/dim]")

    # Find PDF
    pdf_search_paths = [Path("/Users/ilya/Zotero/storage")]
    pdf_path = pdf_extractor.find_pdf(
        citekey, pdf_search_paths,
        entry_title=entry.title or "",
        direct_path=source.get("pdf_path") if source else entry.pdf_path,
        pdf_lookup={k: v.pdf_path for k, v in entry_lookup.items() if v.pdf_path},
    )

    if not pdf_path:
        console.print("  [red]PDF not found[/red]")
        return

    # Extract text
    pdf_text = pdf_extractor.extract(pdf_path)
    if not pdf_text or len(pdf_text) < cfg.processing.min_pdf_length:
        console.print("  [red]PDF extraction failed or text too short[/red]")
        return

    # Extract fragments
    result = extract_fragments(entry, pdf_text, cfg, state, ai)

    if not result or not result.fragments:
        console.print("  [red]No fragments extracted[/red]")
        return

    console.print(f"  [green]{len(result.fragments)} fragments[/green]", end="")

    # Save to vault
    saved_path = save_fragments_to_vault(
        citekey, result.fragments, vault,
        entry=entry, config=cfg, state=state,
        pdf_text=pdf_text, ai=ai, entry_lookup=entry_lookup,
    )
    if saved_path:
        console.print(f" → @{citekey}")
    else:
        console.print(" [dim](DB only)[/dim]")


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="Show full detailed tables")
@click.option("--chapter", "-ch", type=int, help="Filter by chapter")
@click.pass_context
def status(ctx, verbose, chapter):
    """Unified status: processing, coverage, gaps, reference gaps."""
    config_path = ctx.obj["config_path"]
    cfg, state, vault = _init_components(config_path)
    _sync_sections(cfg, state, vault, quiet=True)

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

    # --- Coverage by chapter ---
    table = Table(title="Coverage by Chapter", show_edge=False, pad_edge=False)
    table.add_column("Chapter", style="cyan")
    table.add_column("Sources", justify="right", width=8)
    for ch in range(1, 5):
        if chapter and ch != chapter:
            continue
        count = cov["chapters"].get(ch, 0)
        style = "green" if count >= 10 else "yellow" if count >= 5 else "red"
        name = cfg.dissertation.chapters.get(ch, "")
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
    min_sources = cfg.dissertation.min_sources_per_section
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
@click.pass_context
def research(ctx, section, no_save, force):
    """Deep section analysis — argument structure, citation plan, gaps.

    Auto-processes unextracted sources before analysis.
    Use --force to re-extract all fragments.

    Example: klemma research --section 1.3.2
    """
    config_path = ctx.obj["config_path"]
    cfg, state, vault = _init_components(config_path)
    _sync_sections(cfg, state, vault)
    ai = _init_ai(cfg)

    from .skills.researcher import pre_extract_sources, research_section

    chapter = int(section.split(".")[0])

    # Auto-process unextracted sources
    console.print(f"[blue]Auto-processing unextracted sources for section {section}...[/blue]")
    extract_result = pre_extract_sources(
        section, chapter, cfg, state, vault, ai,
        force=force,
        on_progress=lambda ck, st, i, n: console.print(
            f"  [{i}/{n}] @{ck}: {st}"
        ),
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
        mode_label = "[magenta]Инкрементальное обновление[/magenta]"
        details = []
        if prev["user_notes"]:
            details.append("заметки пользователя")
        details.append(f"пред. фрагментов: {prev['previous_fragment_count']}")
        console.print(f"\n{mode_label} раздела {section} ({', '.join(details)})")
    else:
        console.print(f"\n[blue]Первичный анализ раздела {section}...[/blue]")

    result = research_section(section, cfg, state, vault, ai, save_to_vault=not no_save)

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
    cfg, state, vault = _init_components(config_path)

    result = _sync_sections(cfg, state, vault, quiet=True)

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
            name = cfg.dissertation.chapters.get(ch, "")
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
    import subprocess

    config_path = ctx.obj["config_path"]
    cfg, state, vault = _init_components(config_path)

    from .skills.agent import build_agent_context

    console.print("[blue]Сборка контекста исследования...[/blue]")
    context = build_agent_context(cfg, state, vault, section=section, chapter=chapter)

    console.print(f"[dim]Query: {query}[/dim]")
    console.print("[blue]Запуск агента...[/blue]\n")

    # Launch Claude interactively — stdin/stdout pass through
    subprocess.run(["claude", "--system-prompt", context, query])

    console.print("\n[dim]Сессия агента завершена.[/dim]")


@main.command()
@click.option("--section", "-s", help="Focus on a specific section (recommend mode)")
@click.option("--audit", is_flag=True, help="Deep quality audit")
@click.pass_context
def library(ctx, section, audit):
    """AI-powered library analysis and recommendations.

    Without flags: overall health assessment.
    With --section: reading recommendations for that section.
    With --audit: deep quality audit.
    """
    config_path = ctx.obj["config_path"]
    cfg, state, vault = _init_components(config_path)
    _sync_sections(cfg, state, vault)
    ai = _init_ai(cfg)

    from .literature.pdf import PDFExtractor
    from .skills.librarian import analyze_library

    entry_lookup = PDFExtractor.load_entry_lookup(Path(cfg.zotero.library_json)) if cfg.zotero.library_json else {}

    mode = "audit" if audit else "recommend" if section else "status"
    console.print(f"[blue]Analyzing library ({mode})...[/blue]")

    report = analyze_library(cfg, state, vault, ai, entry_lookup, mode=mode, focus_section=section)

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

    # Reference gaps table
    _print_ref_gaps_table(state)

    console.print(f"\n[dim]Full report saved to vault.[/dim]")


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
