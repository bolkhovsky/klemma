"""Guided Serendipity: decisions and briefing commands."""

import sys

import click
from rich.panel import Panel
from rich.table import Table

from ..cli import _get_context, _init_ai, console, main


@main.group(invoke_without_command=True)
@click.pass_context
def decisions(ctx):
    """View and manage research decisions (Guided Serendipity)."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(decisions_list)


@decisions.command("list")
@click.option(
    "--section", "-s", default=None, help="Filter by section (e.g. 3.2)"
)
@click.option(
    "--pending", is_flag=True, help="Show only pending (undecided) decisions"
)
@click.option(
    "--all", "show_all", is_flag=True, help="Include skipped decisions"
)
@click.option("--limit", "-n", default=20, help="Max decisions to show")
@click.pass_context
def decisions_list(ctx, section, pending, show_all, limit):
    """List research decisions."""
    kctx = _get_context(ctx)
    repo = kctx.state.decisions

    if pending:
        items = repo.get_pending_decisions()
    else:
        items = repo.get_decisions(
            section=section, limit=limit, include_skipped=show_all
        )

    if not items:
        console.print("[dim]No decisions yet. Decisions appear after briefings.[/dim]")
        return

    counts = repo.count_decisions()
    console.print(
        f"[bold]Research Trail:[/bold] {counts['decided']} decided, "
        f"{counts['pending']} pending, {counts['skipped']} skipped\n"
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", width=4)
    table.add_column("Date", width=10)
    table.add_column("Type", width=9)
    table.add_column("Source", width=20)
    table.add_column("Choice", width=8)
    table.add_column("Sections", width=10)

    for d in items:
        date = d["created_at"][:10] if d.get("created_at") else ""
        source = d.get("trigger_source") or ""
        if len(source) > 20:
            source = source[:17] + "..."

        choice = d.get("chosen_option")
        if choice is None:
            choice_str = "[yellow]pending[/yellow]"
        elif choice == "__skipped__":
            choice_str = "[dim]skipped[/dim]"
        else:
            choice_str = f"[green]{choice}[/green]"

        sections = d.get("sections")
        sections_str = ", ".join(sections) if isinstance(sections, list) else ""

        table.add_row(
            str(d["id"]),
            date,
            d.get("trigger_type", ""),
            source,
            choice_str,
            sections_str,
        )

    console.print(table)


@decisions.command("show")
@click.argument("decision_id", type=int)
@click.pass_context
def decisions_show(ctx, decision_id):
    """Show details of a specific decision."""
    kctx = _get_context(ctx)
    d = kctx.state.decisions.get_decision(decision_id)

    if not d:
        console.print(f"[red]Decision #{decision_id} not found[/red]")
        return

    # Header
    status = "pending" if d.get("chosen_option") is None else "decided"
    if d.get("chosen_option") == "__skipped__":
        status = "skipped"
    console.print(
        Panel(
            f"[bold]{d.get('trigger_type', '').upper()}[/bold] — "
            f"{'@' + d['trigger_source'] if d.get('trigger_source') else 'library analysis'}\n"
            f"Status: {status} | Created: {d.get('created_at', '')[:16]}",
            title=f"Decision #{d['id']}",
        )
    )

    # Options
    options = d.get("options_json", [])
    if isinstance(options, list):
        console.print("\n[bold]Options:[/bold]")
        for opt in options:
            key = opt.get("key", "?")
            title = opt.get("title", "")
            desc = opt.get("description", "")
            chosen = d.get("chosen_option") == key
            marker = " [green]← chosen[/green]" if chosen else ""
            console.print(f"  [{key}] [bold]{title}[/bold]{marker}")
            if desc:
                console.print(f"      {desc}")

    # Rationale
    if d.get("rationale"):
        console.print(f"\n[bold]Rationale:[/bold] {d['rationale']}")

    # Context summary
    context = d.get("context_json", {})
    if isinstance(context, dict) and context.get("key_claims"):
        console.print("\n[bold]Key claims:[/bold]")
        for claim in context["key_claims"]:
            console.print(f"  • {claim}")

    # Pending action hint
    if d.get("chosen_option") is None:
        console.print(
            f"\n[yellow]Decide:[/yellow] klemma decide {d['id']} A|B|C"
        )


@main.command()
@click.argument("decision_id", type=int)
@click.argument("option", type=str)
@click.option("--reason", "-r", default=None, help="Why you chose this option")
@click.pass_context
def decide(ctx, decision_id, option, reason):
    """Record your choice for a pending decision."""
    kctx = _get_context(ctx)
    repo = kctx.state.decisions

    d = repo.get_decision(decision_id)
    if not d:
        console.print(f"[red]Decision #{decision_id} not found[/red]")
        return

    if d.get("chosen_option") is not None:
        console.print(
            f"[yellow]Decision #{decision_id} already "
            f"{'skipped' if d['chosen_option'] == '__skipped__' else 'decided'}: "
            f"{d['chosen_option']}[/yellow]"
        )
        return

    # Validate option against available options
    options = d.get("options_json", [])
    valid_keys = {opt.get("key") for opt in options if isinstance(opt, dict)}
    if valid_keys and option.upper() not in valid_keys:
        console.print(
            f"[red]Invalid option '{option}'. Valid: {', '.join(sorted(valid_keys))}[/red]"
        )
        return

    updated = repo.decide(decision_id, option.upper(), reason)
    if updated:
        console.print(
            f"[green]✓ Decision #{decision_id} → {option.upper()}[/green]"
        )
        if reason:
            console.print(f"  Rationale: {reason}")
    else:
        console.print(f"[red]Failed to update decision #{decision_id}[/red]")


@main.command()
@click.argument("citekey", required=False)
@click.option("--pending", is_flag=True, help="Process all sources without briefings")
@click.option("--model", default=None, help="Override AI model")
@click.pass_context
def briefing(ctx, citekey, pending, model):
    """Generate a Guided Serendipity briefing for a source.

    Analyzes a newly added source, finds connections in your library,
    and proposes 2-3 research directions (forks) for you to choose from.

    Usage:
      klemma briefing <citekey>     — briefing for specific source
      klemma briefing --pending     — process sources without briefings
    """
    from ..skills.briefer import generate_briefing, save_briefing_as_decision

    kctx = _get_context(ctx)
    cfg, state = kctx.config, kctx.state

    if not citekey and not pending:
        console.print("[red]Provide a citekey or use --pending[/red]")
        return

    if model:
        cfg.ai.model = model
    ai = _init_ai(cfg)
    if not ai:
        console.print("[red]AI backend required for briefing. Configure in klemmarc.[/red]")
        return

    # Determine which sources to brief
    if pending:
        # Find sources that have fragments but no briefing decision
        all_sources = state.sources.get_all_sources()
        existing_briefings = {
            d["trigger_source"]
            for d in state.decisions.get_decisions(trigger_type="briefing", limit=9999)
            if d.get("trigger_source")
        }
        targets = [
            s["id"] for s in all_sources
            if s["id"] not in existing_briefings
            and s.get("fragment_count", 0) > 0
        ]
        if not targets:
            console.print("[dim]No sources pending briefing.[/dim]")
            return
        console.print(f"[blue]{len(targets)} source(s) pending briefing[/blue]\n")
    else:
        targets = [citekey]

    for target_citekey in targets:
        console.print(f"\n[bold]Briefing: @{target_citekey}[/bold]")
        console.print("[dim]Analyzing source, finding connections...[/dim]")

        result = generate_briefing(
            source_citekey=target_citekey,
            config=cfg,
            state=state,
            ai=ai,
            dissertation_context=kctx.dissertation_context,
            embeddings=kctx.embeddings,
            klemma_home=kctx.klemma_home,
            project_root=kctx.project_root,
            language=getattr(cfg.dissertation, "language", "Russian") if cfg.dissertation else "Russian",
        )

        if result.error:
            console.print(f"[red]Error: {result.error}[/red]")
            continue

        # Display briefing
        _display_briefing(result)

        # Save as decision
        decision_id = save_briefing_as_decision(result, state)
        if decision_id is None:
            console.print("[yellow]No forks generated — skipping decision.[/yellow]")
            continue

        # Interactive choice if TTY
        if sys.stdin.isatty() and len(targets) <= 3:
            _interactive_decide(state, decision_id, result)
        else:
            console.print(
                f"\n[yellow]Decision #{decision_id} saved as pending.[/yellow]"
                f"\nDecide later: klemma decide {decision_id} A|B|C"
            )


def _display_briefing(result):
    """Display briefing results in the terminal."""
    # Key claims
    if result.key_claims:
        console.print("\n[bold]Key claims:[/bold]")
        for claim in result.key_claims:
            console.print(f"  • {claim}")

    # Connections
    if result.connections:
        console.print("\n[bold]Connections:[/bold]")
        for conn in result.connections:
            rel = conn.get("relationship", "related")
            ckey = conn.get("related_citekey", "?")
            desc = conn.get("description", "")
            console.print(f"  {rel}: {ckey} — {desc}")

    # Niches
    if result.niches:
        console.print("\n[bold]Niches/gaps:[/bold]")
        for niche in result.niches:
            console.print(f"  → {niche}")

    # Forks
    if result.forks:
        console.print("\n[bold]── Fork ──[/bold]")
        for fork in result.forks:
            key = fork.get("key", "?")
            title = fork.get("title", "")
            desc = fork.get("description", "")
            sections = fork.get("sections", [])
            sec_str = f" (sections: {', '.join(sections)})" if sections else ""
            console.print(f"  [{key}] [bold]{title}[/bold]{sec_str}")
            if desc:
                console.print(f"      {desc}")


def _interactive_decide(state, decision_id, result):
    """Prompt the user to choose a fork interactively."""
    valid_keys = [f.get("key", "?") for f in result.forks]
    keys_str = "/".join(valid_keys)

    console.print(f"\n[yellow]Choose direction [{keys_str}/skip]:[/yellow] ", end="")
    try:
        choice = input().strip().upper()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Skipped[/dim]")
        return

    if choice in ("SKIP", "S", ""):
        state.decisions.skip_decision(decision_id)
        console.print("[dim]Skipped — you can revisit later with 'klemma decisions --pending'[/dim]")
    elif choice in valid_keys:
        # Ask for rationale
        console.print("[dim]Why? (optional, press Enter to skip):[/dim] ", end="")
        try:
            reason = input().strip() or None
        except (EOFError, KeyboardInterrupt):
            reason = None
        state.decisions.decide(decision_id, choice, reason)
        console.print(f"[green]✓ Decision #{decision_id} → {choice}[/green]")
    else:
        console.print(f"[red]Invalid choice. Saved as pending (decide later: klemma decide {decision_id} {keys_str})[/red]")


@decisions.command("trail")
@click.pass_context
def decisions_trail(ctx):
    """Show the research trail — chronological path of decisions."""
    kctx = _get_context(ctx)
    trail = kctx.state.decisions.get_trail()

    if not trail:
        console.print("[dim]No decisions made yet. Your research trail is empty.[/dim]")
        return

    console.print(f"[bold]Research Trail[/bold] — {len(trail)} decisions\n")

    for i, d in enumerate(trail):
        prefix = "└─" if i == len(trail) - 1 else "├─"
        source = f"@{d['trigger_source']}" if d.get("trigger_source") else "library"
        date = d["created_at"][:10] if d.get("created_at") else ""

        # Find chosen option title
        choice_title = d.get("chosen_option", "?")
        options = d.get("options_json", [])
        if isinstance(options, list):
            for opt in options:
                if isinstance(opt, dict) and opt.get("key") == d.get("chosen_option"):
                    choice_title = opt.get("title", choice_title)
                    break

        console.print(
            f"  {prefix} [{date}] {d['trigger_type']}: {source} "
            f"→ [green]{choice_title}[/green]"
        )
        if d.get("rationale"):
            pad = "   " if i == len(trail) - 1 else "│  "
            console.print(f"  {pad} [dim]{d['rationale']}[/dim]")
