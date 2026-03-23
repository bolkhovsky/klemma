"""Guided Serendipity: decisions and briefing commands."""

import sys

import click
from rich.panel import Panel
from rich.table import Table

from ..cli import _get_context, _init_ai, console, main


@main.group(invoke_without_command=True)
@click.option("--pending", is_flag=True, help="Show only pending decisions")
@click.pass_context
def decisions(ctx, pending):
    """View and manage research decisions (Guided Serendipity)."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(decisions_list, pending=pending)


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
    table.add_column("Summary", width=40)
    table.add_column("Choice", width=8)
    table.add_column("Sections", width=12)

    for d in items:
        date = d["created_at"][:10] if d.get("created_at") else ""

        # Build summary: for insights show first option title, for briefings show citekey
        summary = ""
        if d.get("trigger_source"):
            summary = f"@{d['trigger_source']}"
        else:
            options = d.get("options_json", [])
            if isinstance(options, list) and options:
                summary = options[0].get("title", "")
        if len(summary) > 40:
            summary = summary[:37] + "..."

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
            summary,
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

    # Curated insight fields (WHY / WHERE / tag)
    context = d.get("context_json", {})
    if isinstance(context, dict):
        if context.get("title"):
            console.print(f"\n[bold]{context['title']}[/bold]")
        if context.get("explanation"):
            console.print(f"\n[bold]Why:[/bold] {context['explanation']}")
        if context.get("trajectory"):
            console.print(f"[bold]Where this leads:[/bold] {context['trajectory']}")
        if context.get("diversity_tag"):
            tag = context["diversity_tag"]
            tag_colors = {"methodology": "blue", "bridge": "magenta", "gap": "yellow", "anomaly": "red"}
            tc = tag_colors.get(tag, "white")
            console.print(f"[bold]Tag:[/bold] [{tc}]{tag}[/{tc}]")

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

    # Research note
    if d.get("note"):
        console.print(f"\n[bold]Research note:[/bold] {d['note']}")

    # Feedback
    if d.get("feedback"):
        fb_style = "[green]useful[/green]" if d["feedback"] == "like" else "[yellow]not useful[/yellow]"
        console.print(f"\n[bold]Feedback:[/bold] {fb_style}")

    # Context summary (briefing key_claims)
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
@click.option("--pending", is_flag=True, help="Process sources without briefings (most relevant first)")
@click.option("--limit", "-n", default=10, help="Max sources to brief in --pending mode")
@click.option("--model", default=None, help="Override AI model")
@click.pass_context
def briefing(ctx, citekey, pending, limit, model):
    """Generate a Guided Serendipity briefing for a source.

    Analyzes a newly added source, finds connections in your library,
    and proposes 2-3 research directions (forks) for you to choose from.

    Usage:
      klemma briefing <citekey>        — briefing for specific source
      klemma briefing --pending        — top 10 most relevant unbriefed sources
      klemma briefing --pending -n 5   — top 5
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
        unbriefed = [
            s for s in all_sources
            if s["id"] not in existing_briefings
            and s.get("fragment_count", 0) > 0
        ]
        if not unbriefed:
            console.print("[dim]No sources pending briefing.[/dim]")
            return

        # Sort by relevance: quality_score desc, fragment_count desc, year desc
        unbriefed.sort(
            key=lambda s: (
                s.get("quality_score") or 0,
                s.get("fragment_count") or 0,
                s.get("year") or 0,
            ),
            reverse=True,
        )
        targets = [s["id"] for s in unbriefed[:limit]]
        console.print(
            f"[blue]{len(unbriefed)} source(s) pending briefing, "
            f"processing top {len(targets)}[/blue]\n"
        )
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


@main.command()
@click.option("--raw", is_flag=True, help="Show all raw candidates without LLM curation")
@click.option("--model", default=None, help="Override AI model for curation")
@click.pass_context
def insights(ctx, raw, model):
    """Analyze your library for blind spots and hidden connections.

    Default: LLM-curated top 3-5 insights with trajectories.
    Use --raw for unfiltered candidates (old behavior, no AI).

    Pipeline: generate broadly → suppress heuristically → curate with LLM.
    """
    from ..skills.insights import (
        generate_curated_insights,
        generate_insights,
        save_insights_as_decisions,
    )

    kctx = _get_context(ctx)
    state = kctx.state
    cfg = kctx.config

    if raw:
        # Raw mode — old behavior, no AI
        console.print("[dim]Scanning library for patterns...[/dim]\n")
        result = generate_insights(state, kctx.project_store)

        if not result.blind_spots and not result.hidden_clusters:
            console.print("[green]No issues found. Library looks balanced.[/green]")
            return

        _display_raw_insights(result)

        decision_ids = save_insights_as_decisions(result, state)
        if decision_ids:
            console.print(
                f"\n[yellow]{len(decision_ids)} insight(s) saved as pending decisions.[/yellow]"
                f"\nReview: klemma decisions --pending"
            )
        return

    # Curated mode
    if model:
        cfg.ai.model = model

    ai = None
    try:
        ai = _init_ai(cfg)
    except Exception:
        pass

    console.print("[dim]Scanning library for patterns...[/dim]\n")
    result = generate_curated_insights(
        state,
        config=cfg,
        ai=ai,
        project_store=kctx.project_store,
        dissertation_context=kctx.dissertation_context,
        klemma_home=kctx.klemma_home,
        project_root=kctx.project_root,
        project_chain=getattr(kctx, "project_chain", []),
        language=getattr(cfg.dissertation, "language", "Russian") if cfg.dissertation else "Russian",
        raw_mode=False,
    )

    if result.blocked:
        console.print(
            f"[yellow]{result.pending_count} pending insight(s) — "
            f"resolve them before generating new ones.[/yellow]"
            f"\nReview: klemma decisions --pending"
        )
        return

    if result.raw_count == 0:
        console.print("[green]No issues found. Library looks balanced.[/green]")
        return

    if not result.insights:
        # No curated insights (maybe no AI or all suppressed)
        console.print(
            f"[dim]Found {result.raw_count} raw candidates, "
            f"suppressed {result.suppressed_count}.[/dim]"
        )
        if result.curated_count > 0:
            console.print(
                f"\n[yellow]{result.curated_count} insight(s) saved as pending decisions.[/yellow]"
                f"\nReview: klemma decisions --pending"
            )
        return

    # Display curated insights as Rich panels
    console.print(
        f"[bold]Curated Insights[/bold] — {len(result.insights)} of "
        f"{result.raw_count} candidates "
        f"[dim]({result.suppressed_count} suppressed)[/dim]\n"
    )

    for i, insight in enumerate(result.insights):
        tag_colors = {
            "methodology": "blue",
            "bridge": "magenta",
            "gap": "yellow",
            "anomaly": "red",
        }
        tag_color = tag_colors.get(insight.diversity_tag, "white")

        # Get the real decision ID
        did = result.decision_ids[i] if i < len(result.decision_ids) else "?"

        body_lines = []
        if insight.explanation:
            body_lines.append(f"[bold]Why:[/bold] {insight.explanation}")
        if insight.trajectory:
            body_lines.append(f"[bold]Where this leads:[/bold] {insight.trajectory}")

        sections_str = ", ".join(insight.sections) if insight.sections else ""
        if sections_str:
            body_lines.append(f"[dim]Sections: {sections_str}[/dim]")

        # Options
        for opt in insight.options:
            body_lines.append(
                f"  [{opt['key']}] [bold]{opt['title']}[/bold] — {opt.get('description', '')}"
            )

        body_lines.append(f"\n[dim]klemma decide {did} A|B|C[/dim]")

        panel_content = "\n".join(body_lines)
        console.print(Panel(
            panel_content,
            title=f"Decision #{did} — {insight.title}",
            subtitle=f"[{tag_color}]{insight.diversity_tag}[/{tag_color}]",
            border_style=tag_color,
        ))

    if result.curated_count > 0:
        console.print(
            f"\n[yellow]{result.curated_count} insight(s) saved as pending decisions.[/yellow]"
            f"\nDecide: klemma decide <ID> A|B|C"
        )


def _display_raw_insights(result):
    """Display raw insights (old behavior for --raw mode)."""
    if result.blind_spots:
        console.print(f"[bold]Blind Spots[/bold] ({len(result.blind_spots)} sections)\n")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Section", width=10)
        table.add_column("Sources", width=8, justify="right")
        table.add_column("Average", width=8, justify="right")
        table.add_column("Gaps", width=6, justify="right")
        table.add_column("Severity", width=8)

        for spot in result.blind_spots:
            sev_style = {"high": "[red]high[/red]", "medium": "[yellow]medium[/yellow]"}.get(
                spot.severity, spot.severity
            )
            table.add_row(
                spot.section,
                str(spot.source_count),
                str(spot.average_count),
                str(spot.gap_count),
                sev_style,
            )
        console.print(table)

    if result.hidden_clusters:
        console.print(f"\n[bold]Hidden Clusters[/bold] ({len(result.hidden_clusters)} pairs)\n")
        for c in result.hidden_clusters:
            console.print(
                f"  @{c.citekey_a} ({c.section_a}) ↔ @{c.citekey_b} ({c.section_b})"
                f"  [dim]similarity: {c.similarity}[/dim]"
            )
            if c.title_a and c.title_b:
                console.print(f"    {c.title_a[:60]}")
                console.print(f"    {c.title_b[:60]}")
            console.print()


@decisions.command("note")
@click.argument("decision_id", type=int)
@click.argument("text", type=str)
@click.pass_context
def decisions_note(ctx, decision_id, text):
    """Add a research note to a decision.

    Captures the researcher's thinking — ideas, experiments, hypotheses
    that arise from an insight. These notes feed into future briefings
    and insights.

    Usage:
      klemma decisions note 5 "сопоставить IIEE с SPS, эксперимент: 5 кейсов"
    """
    kctx = _get_context(ctx)
    repo = kctx.state.decisions

    d = repo.get_decision(decision_id)
    if not d:
        console.print(f"[red]Decision #{decision_id} not found[/red]")
        return

    if repo.add_note(decision_id, text):
        console.print(f"[green]✓ Note added to decision #{decision_id}[/green]")
        console.print(f"  [dim]{text}[/dim]")
    else:
        console.print(f"[red]Failed to add note to decision #{decision_id}[/red]")


@decisions.command("like")
@click.argument("decision_id", type=int)
@click.pass_context
def decisions_like(ctx, decision_id):
    """Mark an insight as useful (retrospective feedback).

    Usage:
      klemma decisions like 5
    """
    kctx = _get_context(ctx)
    repo = kctx.state.decisions

    d = repo.get_decision(decision_id)
    if not d:
        console.print(f"[red]Decision #{decision_id} not found[/red]")
        return

    if repo.set_feedback(decision_id, "like"):
        console.print(f"[green]✓ Decision #{decision_id} marked as useful[/green]")
    else:
        console.print(f"[red]Failed to update decision #{decision_id}[/red]")


@decisions.command("dislike")
@click.argument("decision_id", type=int)
@click.pass_context
def decisions_dislike(ctx, decision_id):
    """Mark an insight as not useful (retrospective feedback).

    Usage:
      klemma decisions dislike 5
    """
    kctx = _get_context(ctx)
    repo = kctx.state.decisions

    d = repo.get_decision(decision_id)
    if not d:
        console.print(f"[red]Decision #{decision_id} not found[/red]")
        return

    if repo.set_feedback(decision_id, "dislike"):
        console.print(f"[yellow]✗ Decision #{decision_id} marked as not useful[/yellow]")
    else:
        console.print(f"[red]Failed to update decision #{decision_id}[/red]")


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
