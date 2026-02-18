"""Klemma CLI — dual-mode: headless commands + TUI dashboard."""

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

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


@click.group(invoke_without_command=True)
@click.version_option(version="0.1.0")
@click.option("--config", "-c", default="config.yaml", help="Config file path")
@click.pass_context
def main(ctx, config):
    """Klemma — AI academic assistant.

    Run without arguments to launch TUI dashboard.
    """
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config

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
def morning(ctx):
    """Утренний брифинг — план дня по философии Second Brain."""
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
@click.argument("citekey")
@click.pass_context
def extract(ctx, citekey):
    """Extract citation fragments from a source PDF.

    CITEKEY: Citation key of the source (e.g., smithIceNet2021)
    """
    config_path = ctx.obj["config_path"]
    cfg, state, vault = _init_components(config_path)
    ai = _init_ai(cfg)

    from .literature.pdf import PDFExtractor
    from .skills.extractor import extract_fragments

    pdf_extractor = PDFExtractor(max_chars=cfg.ai.max_pdf_chars)

    # Find source
    source = state.get_source(citekey)
    if not source:
        # Try registering it first
        state.register_sources([citekey])
        source = state.get_source(citekey)

    console.print(f"[blue]Extracting fragments from: {citekey}[/blue]")

    # Load entry lookup for rich metadata and PDF paths
    entry_lookup = PDFExtractor.load_entry_lookup(Path(cfg.zotero.library_json)) if cfg.zotero.library_json else {}

    # Auto-resolve previously detected reference gaps against current library
    resolved = state.resolve_gaps(entry_lookup)
    if resolved:
        console.print(f"[green]Auto-resolved {resolved} reference gap(s)[/green]")

    entry = entry_lookup.get(citekey)
    if not entry:
        from .literature.models import ZoteroEntry
        entry = ZoteroEntry(id=citekey, title=citekey)

    # Find PDF
    pdf_search_paths = [Path("/Users/ilya/Zotero/storage")]
    pdf_path = pdf_extractor.find_pdf(
        citekey, pdf_search_paths,
        entry_title=entry.title or "",
        direct_path=source.get("pdf_path") if source else entry.pdf_path,
        pdf_lookup={k: v.pdf_path for k, v in entry_lookup.items() if v.pdf_path},
    )

    if not pdf_path:
        console.print("[red]PDF not found.[/red]")
        console.print("[dim]Searched in Zotero storage.[/dim]")
        return

    console.print(f"[green]Found PDF:[/green] {pdf_path.name}")

    # Extract text
    pdf_text = pdf_extractor.extract(pdf_path)
    if not pdf_text or len(pdf_text) < cfg.processing.min_pdf_length:
        console.print("[red]PDF extraction failed or text too short.[/red]")
        return

    console.print(f"[dim]Extracted {len(pdf_text)} chars[/dim]")

    # Extract fragments
    console.print("[blue]Analyzing with Claude...[/blue]")
    result = extract_fragments(entry, pdf_text, cfg, state, ai)

    if not result or not result.fragments:
        console.print("[red]No fragments extracted.[/red]")
        return

    # Display results
    console.print(f"\n[green]Extracted {len(result.fragments)} fragments[/green]")
    if result.summary:
        console.print(f"\n[dim]{result.summary}[/dim]")

    table = Table(title=f"Fragments: {citekey}")
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("Type", width=12)
    table.add_column("Section", width=8)
    table.add_column("Rel", justify="right", width=3)
    table.add_column("Fragment", max_width=60)
    table.add_column("Usage", max_width=30, style="dim")

    for i, frag in enumerate(result.fragments, 1):
        rel_style = "green" if frag.relevance >= 4 else "yellow" if frag.relevance >= 3 else "dim"
        table.add_row(
            str(i),
            frag.type,
            frag.section or "-",
            f"[{rel_style}]{frag.relevance}[/{rel_style}]",
            frag.text[:60] + ("..." if len(frag.text) > 60 else ""),
            frag.usage_hint[:30] if frag.usage_hint else "",
        )

    console.print(table)

    # Save fragments to vault note (auto-creates with annotation if missing)
    from .skills.extractor import save_fragments_to_vault
    saved_path = save_fragments_to_vault(
        citekey, result.fragments, vault,
        entry=entry, config=cfg, state=state,
        pdf_text=pdf_text, ai=ai, entry_lookup=entry_lookup,
    )
    if saved_path:
        console.print(f"[green]Фрагменты сохранены в vault:[/green] @{citekey}")
    else:
        console.print(f"[yellow]Заметка @{citekey} не найдена в vault — фрагменты только в БД[/yellow]")


@main.command()
@click.pass_context
def stats(ctx):
    """Show processing and fragment statistics."""
    config_path = ctx.obj["config_path"]
    cfg, state, _ = _init_components(config_path)

    # Processing stats
    proc_stats = state.get_stats()
    table = Table(title="Processing Statistics")
    table.add_column("Status", style="cyan")
    table.add_column("Count", justify="right")

    total = proc_stats.get("total", 1)
    styles = {"completed": "green", "pending": "yellow", "failed": "red", "skipped": "dim", "processing": "blue"}
    for status, count in proc_stats.items():
        if status in ("total", "today"):
            continue
        table.add_row(status.title(), f"[{styles.get(status, 'white')}]{count}[/{styles.get(status, 'white')}]")
    table.add_section()
    table.add_row("[bold]Total[/bold]", f"[bold]{total}[/bold]")
    table.add_row("Today", str(proc_stats.get("today", 0)))
    console.print(table)

    # Fragment stats
    frag_stats = state.get_fragment_stats()
    if frag_stats["total"] > 0:
        console.print()
        table = Table(title="Fragment Statistics")
        table.add_column("Category", style="cyan")
        table.add_column("Count", justify="right")
        table.add_row("Total fragments", str(frag_stats["total"]))
        for ftype, cnt in sorted(frag_stats["by_type"].items()):
            table.add_row(f"  {ftype}", str(cnt))
        console.print(table)


@main.command()
@click.pass_context
def coverage(ctx):
    """Show dissertation coverage by chapter and section."""
    config_path = ctx.obj["config_path"]
    cfg, state, _ = _init_components(config_path)

    cov = state.get_coverage_stats()

    table = Table(title="Coverage by Chapter")
    table.add_column("Chapter", style="cyan")
    table.add_column("Sources", justify="right")
    for ch in range(1, 5):
        count = cov["chapters"].get(ch, 0)
        style = "green" if count >= 10 else "yellow" if count >= 5 else "red"
        name = cfg.dissertation.chapters.get(ch, "")
        table.add_row(f"Ch {ch}: {name}", f"[{style}]{count}[/{style}]")
    console.print(table)

    if cov["sections"]:
        console.print()
        table = Table(title="Coverage by Section")
        table.add_column("Section", style="cyan")
        table.add_column("Sources", justify="right")
        for section, count in sorted(cov["sections"].items()):
            style = "green" if count >= 3 else "yellow" if count >= 1 else "red"
            table.add_row(section, f"[{style}]{count}[/{style}]")
        console.print(table)


@main.command()
@click.option("--min-sources", "-m", type=int, default=3)
@click.pass_context
def gaps(ctx, min_sources):
    """Find sections with insufficient source coverage and reference gaps."""
    config_path = ctx.obj["config_path"]
    cfg, state, _ = _init_components(config_path)

    # Coverage gaps
    gaps_data = state.get_gaps(min_sources=min_sources)
    if not gaps_data:
        console.print(f"[green]All sections have >= {min_sources} sources.[/green]")
    else:
        table = Table(title=f"Sections with < {min_sources} sources")
        table.add_column("Section", style="cyan")
        table.add_column("Count", justify="right")
        table.add_column("Gap", justify="right", style="red")
        for gap in gaps_data:
            needed = min_sources - gap["count"]
            table.add_row(gap["section"], str(gap["count"]), f"-{needed}")
        console.print(table)

    # Reference gaps
    ref_gaps = state.get_reference_gaps(limit=20)
    if ref_gaps:
        console.print()
        ref_table = Table(title="Reference Gaps (missing from library)")
        ref_table.add_column("#", justify="right", style="dim", width=3)
        ref_table.add_column("Score", justify="right", width=6)
        ref_table.add_column("Count", justify="right", width=5)
        ref_table.add_column("Authors", width=20)
        ref_table.add_column("Year", width=5)
        ref_table.add_column("Title", max_width=35)
        ref_table.add_column("Sections", width=10, style="cyan")
        ref_table.add_column("Why", max_width=30, style="dim")

        for i, g in enumerate(ref_gaps, 1):
            sections = g.get("dissertation_sections") or ""
            # Parse concatenated JSON arrays
            if sections and sections.startswith("["):
                import json as _json
                try:
                    sections = ", ".join(_json.loads(sections))
                except (ValueError, TypeError):
                    pass
            score_style = "red bold" if g["score"] >= 10 else "yellow" if g["score"] >= 5 else "dim"
            ref_table.add_row(
                str(i),
                f"[{score_style}]{g['score']:.1f}[/{score_style}]",
                str(g["count"]),
                (g["ref_authors"] or "")[:20],
                str(g.get("ref_year") or ""),
                (g["ref_title"] or "")[:35],
                str(sections)[:10],
                (g.get("why_relevant") or "")[:30],
            )
        console.print(ref_table)
    else:
        console.print("\n[dim]No reference gaps tracked yet.[/dim]")


@main.command()
@click.option("--chapter", "-ch", type=int, help="Filter by chapter")
@click.option("--section", "-s", help="Filter by section")
@click.option("--type", "-t", "frag_type", help="Filter by fragment type")
@click.option("--limit", "-n", type=int, default=20)
@click.pass_context
def fragments(ctx, chapter, section, frag_type, limit):
    """Browse extracted fragments."""
    config_path = ctx.obj["config_path"]
    cfg, state, _ = _init_components(config_path)

    frags = state.get_fragments(
        chapter=chapter, section=section, fragment_type=frag_type, limit=limit
    )

    if not frags:
        console.print("[yellow]No fragments found.[/yellow]")
        return

    table = Table(title=f"Fragments ({len(frags)} shown)")
    table.add_column("Source", width=20, style="cyan")
    table.add_column("Type", width=12)
    table.add_column("Section", width=8)
    table.add_column("Rel", justify="right", width=3)
    table.add_column("Fragment", max_width=50)

    for f in frags:
        table.add_row(
            f.get("citekey", "?")[:20],
            f.get("fragment_type", "?"),
            f.get("section", "-"),
            str(f.get("relevance_score", "?")),
            (f.get("fragment_text", ""))[:50],
        )
    console.print(table)


@main.command()
@click.option("--section", "-s", required=True, help="Идентификатор раздела, например 1.3.2")
@click.option("--no-save", is_flag=True, help="Не сохранять в vault")
@click.option("--force", is_flag=True, help="Переизвлечь фрагменты даже если уже есть")
@click.pass_context
def research(ctx, section, no_save, force):
    """Исследовательский брифинг — анализ раздела перед написанием.

    Собирает контекст из vault и базы данных, анализирует готовность
    раздела и предлагает структуру аргументации с планом цитирования.

    Автоматически извлекает фрагменты из источников раздела, если они
    ещё не были извлечены. Флаг --force переизвлекает все фрагменты.

    Пример: klemma research --section 1.3.2
    """
    config_path = ctx.obj["config_path"]
    cfg, state, vault = _init_components(config_path)
    ai = _init_ai(cfg)

    from .skills.researcher import pre_extract_sources, research_section

    chapter = int(section.split(".")[0])

    # Авто-экстракция фрагментов
    console.print(f"[blue]Подготовка фрагментов для раздела {section}...[/blue]")
    extract_result = pre_extract_sources(
        section, chapter, cfg, state, vault, ai,
        force=force,
        on_progress=lambda ck, status, i, n: console.print(
            f"  [{i}/{n}] @{ck}: {status}"
        ),
    )

    if extract_result["extracted"] > 0:
        console.print(f"[green]Извлечено: {extract_result['extracted']} источников[/green]")
    if extract_result["no_pdf"]:
        for ck in extract_result["no_pdf"]:
            console.print(f"  [yellow]@{ck}: PDF не найден[/yellow]")
    if extract_result["skipped"] > 0 and extract_result["extracted"] == 0:
        console.print(f"[dim]Все {extract_result['skipped']} источников уже извлечены[/dim]")

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


@main.command()
@click.option("--with-queue", is_flag=True, help="Also populate reading queue from high-priority sources")
@click.pass_context
def prepopulate(ctx, with_queue):
    """Import existing vault notes into klemma database.

    Scans @*.md files in the vault's notes folder, reads YAML frontmatter,
    and registers each source with its metadata (chapter, section, quality, etc.).
    """
    config_path = ctx.obj["config_path"]
    cfg, state, vault = _init_components(config_path)

    notes_folder = cfg.obsidian.notes_folder
    note_names = vault.list_notes(notes_folder)

    # Filter to @citekey.md notes only
    citekey_notes = [n for n in note_names if n.startswith("@")]

    if not citekey_notes:
        console.print(f"[yellow]No @citekey notes found in {notes_folder}/[/yellow]")
        return

    console.print(f"[blue]Scanning {notes_folder}/ ...[/blue]")

    imported = 0
    skipped = 0
    by_chapter: dict[int, int] = {}
    by_priority: dict[str, int] = {}
    queue_added = 0

    for note_name in citekey_notes:
        props = vault.get_properties(note_name)
        if not props:
            skipped += 1
            continue

        citekey = props.get("citekey", note_name.lstrip("@"))
        quality = props.get("quality", 0)
        priority = props.get("priority", "medium")
        chapter = props.get("chapter")
        section = props.get("section", "")
        nr1 = props.get("relevance_nr1", 0)
        nr2 = props.get("relevance_nr2", 0)

        # Normalize
        if isinstance(quality, str):
            quality = int(quality.split("/")[0]) if "/" in quality else int(quality)
        if isinstance(chapter, str):
            chapter = int(chapter) if chapter.isdigit() else None

        state.register_sources([citekey])
        state.update_source_metadata(
            source_id=citekey,
            quality_score=quality or 0,
            primary_chapter=chapter,
            primary_section=str(section) if section else None,
            relevance_nr1=nr1 or 0,
            relevance_nr2=nr2 or 0,
            citation_priority=priority or "medium",
            note_path=f"{notes_folder}/{note_name}.md",
        )

        # Мульти-секции: sections=[1.1, 1.4.1, ...], chapters=[1, 3]
        sections_list = props.get("sections", [])
        chapters_list = props.get("chapters", [])
        if isinstance(sections_list, list) and sections_list:
            str_sections = [str(s) for s in sections_list]
            int_chapters = [int(c) for c in chapters_list] if isinstance(chapters_list, list) else []
            state.set_source_sections(citekey, str_sections, int_chapters)

        if chapter:
            by_chapter[chapter] = by_chapter.get(chapter, 0) + 1
        by_priority[priority or "medium"] = by_priority.get(priority or "medium", 0) + 1

        if with_queue and priority == "high":
            state.add_to_reading_queue(citekey, priority=80)
            queue_added += 1

        imported += 1

    # Summary
    console.print(f"\n[green]Imported {imported} sources from vault.[/green]")
    if skipped:
        console.print(f"[dim]Skipped {skipped} notes (no frontmatter).[/dim]")

    if by_chapter:
        console.print()
        table = Table(title="Coverage by Chapter")
        table.add_column("Chapter", style="cyan")
        table.add_column("Sources", justify="right")
        for ch in sorted(by_chapter):
            name = cfg.dissertation.chapters.get(ch, "")
            table.add_row(f"Ch {ch}: {name}", str(by_chapter[ch]))
        console.print(table)

    if by_priority:
        parts = [f"{p}: {c}" for p, c in sorted(by_priority.items())]
        console.print(f"\n[dim]Priority: {', '.join(parts)}[/dim]")

    if queue_added:
        console.print(f"[blue]Reading queue: {queue_added} high-priority papers added.[/blue]")


@main.command()
@click.argument("query")
@click.option("--section", "-s", help="Фокус на конкретном разделе")
@click.option("--chapter", "-ch", type=int, help="Фокус на конкретной главе")
@click.pass_context
def agent(ctx, query, section, chapter):
    """Universal research agent with full dissertation context.

    Launches Claude Code interactively with all research data as context.
    Claude gets full tool access (web search, file I/O, follow-up questions).

    Example: klemma agent "Какие основные методы валидации прогнозов?"
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


if __name__ == "__main__":
    main()
