"""CLI wrapper for meeting-report ingestion (Bonum portal MVP).

Thin Click layer over the pure domain logic in ``klemma.meetings``.
"""

from __future__ import annotations

from pathlib import Path

import click

from ..cli import _get_context, console, main
from ..meetings import import_meeting, parse_protocol


@main.group()
def meetings():
    """Meeting-report portal (Bonum MVP): import & inspect protocols."""


@meetings.command("import")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--no-embed", is_flag=True, help="Skip embedding after import")
@click.pass_context
def meetings_import(ctx, path: Path, no_embed: bool):
    """Import meeting protocol(s) from a markdown file or a directory of them."""
    kctx = _get_context(ctx)
    state = kctx.state
    embeddings = None if no_embed else kctx.embeddings

    if embeddings is None and not no_embed:
        console.print(
            "[yellow]No embedding backend configured — importing without "
            "embeddings (semantic search / ask will be empty).[/yellow]"
        )

    files = sorted(path.glob("*.md")) if path.is_dir() else [path]
    if not files:
        console.print(f"[red]No .md protocols found in {path}[/red]")
        return

    total_frags = total_emb = 0
    for f in files:
        pm = parse_protocol(f.read_text(encoding="utf-8"))
        result = import_meeting(state, embeddings, pm, f.stem)
        total_frags += result["fragments"]
        total_emb += result["embedded"]
        console.print(
            f"[green]✓[/green] {result['source_id']} — "
            f"{result['fragments']} fragments "
            f"({result['tasks']} tasks), {result['embedded']} embedded"
        )

    console.print(
        f"\n[bold green]Imported {len(files)} meeting(s): "
        f"{total_frags} fragments, {total_emb} embedded.[/bold green]"
    )
