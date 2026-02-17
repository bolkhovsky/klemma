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
    """Generate daily morning plan."""
    config_path = ctx.obj["config_path"]
    cfg, state, vault = _init_components(config_path)
    ai = _init_ai(cfg)

    from .skills.planner import generate_morning_plan

    console.print("[blue]Generating morning plan...[/blue]")

    plan = generate_morning_plan(cfg, state, vault, ai)

    # Display plan
    console.print()
    console.print(Panel(
        f"[bold]Dissertation:[/bold] {plan.dissertation_task}\n\n"
        f"[bold]Assistant:[/bold] {plan.assistant_task}\n\n"
        f"[bold]Reading:[/bold] {plan.reading_target}\n"
        f"[dim]{plan.reading_snippet}[/dim]\n\n"
        f"[bold]Progress:[/bold] {plan.progress_summary}",
        title=f"Plan for today",
        border_style="green",
    ))

    if plan.coverage_gaps:
        console.print("\n[yellow]Coverage gaps:[/yellow]")
        for gap in plan.coverage_gaps:
            console.print(f"  - {gap}")

    # Append to daily note
    daily_content = (
        f"## Klemma Plan\n\n"
        f"**Dissertation:** {plan.dissertation_task}\n\n"
        f"**Assistant:** {plan.assistant_task}\n\n"
        f"**Reading:** {plan.reading_target}\n\n"
        f"> {plan.reading_snippet}\n\n"
        f"**Progress:** {plan.progress_summary}\n"
    )
    vault.append_to_daily(daily_content)
    console.print("\n[dim]Plan appended to daily note.[/dim]")


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

    # Find PDF
    pdf_search_paths = [Path("/Users/ilya/Zotero/storage")]
    pdf_path = pdf_extractor.find_pdf(
        citekey, pdf_search_paths,
        direct_path=source.get("pdf_path") if source else None,
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

    # Build minimal entry
    from .literature.models import ZoteroEntry
    entry = ZoteroEntry(id=citekey, title=citekey)

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
    """Find sections with insufficient source coverage."""
    config_path = ctx.obj["config_path"]
    cfg, state, _ = _init_components(config_path)

    gaps_data = state.get_gaps(min_sources=min_sources)
    if not gaps_data:
        console.print(f"[green]All sections have >= {min_sources} sources.[/green]")
        return

    table = Table(title=f"Sections with < {min_sources} sources")
    table.add_column("Section", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Gap", justify="right", style="red")
    for gap in gaps_data:
        needed = min_sources - gap["count"]
        table.add_row(gap["section"], str(gap["count"]), f"-{needed}")
    console.print(table)


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


if __name__ == "__main__":
    main()
