"""Research, library, and ask commands."""

import re

import click
from rich.panel import Panel
from rich.table import Table

from ..cli import (
    _coach_section_hint,
    _get_context,
    _init_ai,
    _print_ref_gaps_table,
    _sync_sections,
    console,
    main,
)


@main.command()
@click.option(
    "--section",
    "-s",
    required=True,
    help="Section ID (e.g. 1.3.2) or semantic type (e.g. methodology)",
)
@click.option("--no-save", is_flag=True, help="Не сохранять в vault")
@click.option("--force", is_flag=True, help="Переизвлечь фрагменты даже если уже есть")
@click.option(
    "--model", default=None, help="Override AI model (e.g. openai/gpt-4.1-mini)"
)
@click.pass_context
def research(ctx, section, no_save, force, model):
    """Deep section analysis — argument structure, citation plan, gaps.

    Auto-processes unextracted sources before analysis.
    Use --force to re-extract all fragments.

    Example: klemma research --section 1.3.2
    Example: klemma research -s methodology
    """
    kctx = _get_context(ctx)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    _sync_sections(kctx)
    if model:
        cfg.ai.model = model
    ai = _init_ai(cfg)

    from ..config import parse_chapter_from_section
    from ..section_types import resolve_section_identifier
    from ..skills.researcher import pre_extract_sources, research_section

    # Resolve semantic type -> numeric section if possible
    resolved_section, section_type = resolve_section_identifier(section, kctx.project)
    if section_type and resolved_section:
        console.print(
            f"[dim]Resolved {section} \u2192 section {resolved_section} ({section_type.value})[/dim]"
        )
        section = resolved_section
    elif section_type and not resolved_section:
        # Fallback: check DB section_type_map (populated by sync_section_types)
        db_sections = state.get_sections_for_type(section_type.value)
        if db_sections:
            section = db_sections[0]
            console.print(
                f"[dim]Resolved {section_type.value} \u2192 section {section}[/dim]"
            )
        else:
            # Show available types so the user can pick the right one
            all_types = state.get_available_section_types()
            console.print(
                f"[yellow]No sections mapped to '{section_type.value}' in this project.[/yellow]"
            )
            if all_types:
                type_list = ", ".join(all_types)
                console.print(f"[dim]Available types: {type_list}[/dim]")
            else:
                console.print(
                    "[dim]No section types mapped yet. Run klemma status to trigger auto-sync.[/dim]"
                )
            raise SystemExit(1)

    chapter = parse_chapter_from_section(section)

    # Auto-process unextracted sources
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
    )

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[dim]{task.fields[status]}[/dim]"),
        console=console,
        transient=True,
    )
    task_id = progress.add_task(
        f"Extracting fragments for section {section}",
        total=None,
        status="scanning sources...",
    )

    def _on_extract_progress(ck: str, status: str, i: int, n: int) -> None:
        progress.update(task_id, total=n, completed=i, status=f"@{ck}: {status}")

    with progress:
        extract_result = pre_extract_sources(
            section,
            chapter,
            cfg,
            state,
            vault,
            ai,
            force=force,
            library=kctx.library,
            on_progress=_on_extract_progress,
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

    # Check: first run or update
    from ..skills.researcher import _load_previous_research

    prev = _load_previous_research(section, chapter, state, kctx.project_root)
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
            section,
            cfg,
            state,
            vault,
            ai,
            save_to_vault=not no_save,
            project=kctx.project,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
            project_root=kctx.project_root,
            embeddings=kctx.embeddings,
            paper_store=kctx.paper_store,
            user_library=kctx.user_library,
        )

    if not result.section_status:
        console.print("[red]Не удалось сгенерировать брифинг.[/red]")
        return

    console.print()

    # Section status
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

    # Fragment distribution
    if result.fragment_distribution:
        parts = [f"{t}: {c}" for t, c in result.fragment_distribution.items() if c > 0]
        if parts:
            console.print(f"\n[dim]Фрагменты: {', '.join(parts)}[/dim]")

    # Argument structure
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

    # Citation plan
    if result.citation_plan:
        console.print()
        table = Table(title="План цитирования")
        table.add_column("Источник", width=25, style="cyan")
        table.add_column("Тип", width=12)
        table.add_column("Рел", justify="right", width=3)
        table.add_column("Где", max_width=35)
        table.add_column("Фрагмент", max_width=40, style="dim")

        for c in result.citation_plan:
            rel_style = (
                "green" if c.relevance >= 4 else "yellow" if c.relevance >= 3 else "dim"
            )
            table.add_row(
                f"@{c.citekey}",
                c.usage,
                f"[{rel_style}]{c.relevance}[/{rel_style}]",
                c.position[:35] if c.position else "",
                (
                    c.fragment_text[:40] + ("..." if len(c.fragment_text) > 40 else "")
                    if c.fragment_text
                    else ""
                ),
            )
        console.print(table)

    # Coverage gaps
    if result.missing_coverage:
        console.print("\n[yellow]Пробелы в покрытии:[/yellow]")
        gap_sections: list[str] = []
        for m in result.missing_coverage:
            console.print(f"  - {m}")
            # Extract section numbers like 2.3.4 from free-text gap descriptions
            sec_match = re.search(r"\b(\d+(?:\.\d+)+)\b", m)
            if sec_match:
                gap_sections.append(sec_match.group(1))
        if gap_sections:
            console.print("\n[dim]Следующие шаги:[/dim]")
            for sec in dict.fromkeys(gap_sections):  # deduplicate, preserve order
                console.print(f"  [cyan]klemma suggest -s {sec}[/cyan]")

    # Writing suggestions
    if result.writing_suggestions:
        console.print("\n[green]Рекомендации по написанию:[/green]")
        for s in result.writing_suggestions:
            console.print(f"  - {s}")

    # Filtered citekeys
    if result.filtered_citekeys:
        console.print(
            f"\n[yellow]Removed {len(result.filtered_citekeys)} hallucinated citekeys "
            f"(not in library): {result.filtered_citekeys}[/yellow]"
        )

    # Save
    if not no_save:
        console.print(
            f"\n[dim]Брифинг сохранён: notes/research/Research_{section}.md[/dim]"
        )

    # Coach hint (informational, after research)
    hint = _coach_section_hint(state, section, kctx.project_root)
    if hint:
        console.print(f"\n[dim]\U0001f4a1 {hint}[/dim]")


@main.command()
@click.argument("query")
@click.option("--section", "-s", help="Focus on a specific section")
@click.option("--chapter", "-ch", type=int, help="Focus on a specific chapter")
@click.option(
    "--model", default=None, help="Override AI model (e.g. openai/gpt-4.1-mini)"
)
@click.pass_context
def ask(ctx, query, section, chapter, model):
    """Ask a research question with full dissertation context.

    Example: klemma ask "What are the main ice forecast validation methods?"
    """
    kctx = _get_context(ctx)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    if model:
        cfg.ai.model = model
    ai = _init_ai(cfg)

    from ..skills.agent import build_agent_context, update_agents_index

    with console.status("Сборка контекста исследования", spinner="dots"):
        context = build_agent_context(
            cfg,
            state,
            vault,
            section=section,
            chapter=chapter,
            project=kctx.project,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
            project_name=kctx.project_name,
            project_root=kctx.project_root,
            embeddings=kctx.embeddings,
            query=query,
            paper_store=kctx.paper_store,
            user_library=kctx.user_library,
        )

    # Show RAG status
    if kctx.embeddings:
        frag_stats = state.get_fragment_embedding_stats()
        if frag_stats["embedded"] > 0:
            console.print(
                f"[dim]RAG: {frag_stats['embedded']} fragment embeddings available[/dim]"
            )
        else:
            console.print(
                "[dim]RAG: no fragment embeddings (run klemma embed fragments)[/dim]"
            )

    console.print(f"[dim]Query: {query}[/dim]")

    response = None
    if ai.interactive_available:
        import os
        import subprocess as _sp

        # Sanitize env to avoid nested Claude Code session detection (#131)
        clean_env = {
            k: v for k, v in os.environ.items() if k != "CLAUDECODE"
        }
        result = _sp.run(
            [
                "claude",
                "-p",
                "--model",
                cfg.ai.model,
                "--system-prompt",
                context,
                query,
            ],
            capture_output=True,
            text=True,
            timeout=cfg.ai.timeout,
            env=clean_env,
        )
        response = result.stdout
        if response:
            console.print(response)
        else:
            stderr = (result.stderr or "").strip()
            console.print("[red]Не удалось получить ответ.[/red]")
            if stderr:
                console.print(f"[dim]{stderr[:300]}[/dim]")
    else:
        with console.status("Генерация ответа", spinner="dots"):
            from ..ai import resolve_task_model

            response = ai.call(
                system=context,
                user=query,
                max_tokens=8192,
                model_override=resolve_task_model("ask", cfg.ai),
            )
        if response:
            console.print(response)
        else:
            console.print("[red]Не удалось получить ответ.[/red]")

    # Save agent response to notes/agents/
    if response and kctx.project_root:
        from datetime import date as _date

        slug = query[:40].strip().replace(" ", "_").replace("/", "-")
        slug = "".join(c for c in slug if c.isalnum() or c in "_-")
        today = _date.today().isoformat()
        project_tag = f"{kctx.project_name}_" if kctx.project_name else ""
        filename = f"Agent_{project_tag}{today}_{slug}.md"

        agents_dir = kctx.project_root / "notes" / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        save_path = agents_dir / filename

        frontmatter = (
            f"---\ntype: agent\ndate: {today}\n" f'query: "{query[:200]}"\n---\n\n'
        )
        save_path.write_text(frontmatter + response, encoding="utf-8")
        console.print(f"\n[green]Saved: {save_path}[/green]")

        idx = update_agents_index(kctx.project_root)
        if idx:
            console.print("[dim]Updated notes/AGENTS.md[/dim]")

    console.print("[dim]Сессия агента завершена.[/dim]")


# --- Library group ---


@main.group(invoke_without_command=True)
@click.option("--section", "-s", help="Focus on a specific section (recommend mode)")
@click.option("--audit", is_flag=True, help="Deep quality audit")
@click.option(
    "--model", default=None, help="Override AI model (e.g. openai/gpt-4.1-mini)"
)
@click.pass_context
def library(ctx, section, audit, model):
    """AI-powered library analysis and recommendations.

    Without flags: overall health assessment.
    With --section: reading recommendations for that section.
    With --audit: deep quality audit.
    """
    if ctx.invoked_subcommand is not None:
        return

    kctx = _get_context(ctx)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    _sync_sections(kctx)
    if model:
        cfg.ai.model = model
    ai = _init_ai(cfg)

    from ..skills.librarian import analyze_library

    entry_lookup = kctx.library.entries if kctx.library else {}

    mode = "audit" if audit else "recommend" if section else "status"
    if mode == "recommend":
        console.print(
            f"[yellow]Warning: `klemma library -s` is deprecated. Use `klemma research -s {section}`.[/yellow]"
        )

    with console.status(f"Analyzing library ({mode})", spinner="dots"):
        report = analyze_library(
            cfg,
            state,
            vault,
            ai,
            entry_lookup,
            mode=mode,
            focus_section=section,
            project=kctx.project,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
            project_name=kctx.project_name,
            project_root=kctx.project_root,
        )

    if not report:
        console.print("[red]Failed to generate library analysis.[/red]")
        return

    # Overall health
    if report.overall_health:
        console.print(
            Panel(report.overall_health, title="Library Health", border_style="blue")
        )

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
            style = {"high": "red", "medium": "yellow", "low": "dim"}.get(
                priority, "white"
            )
            console.print(
                f"  [{style}]{priority.upper()}[/{style}] {rec.get('action', '')}"
            )
            if rec.get("reason"):
                console.print(f"        [dim]{rec['reason']}[/dim]")

    # Section detail (recommend mode)
    if report.section_detail:
        detail = report.section_detail
        if detail.get("current_sources_assessment"):
            console.print(
                f"\n[bold]Section Assessment[/bold]\n{detail['current_sources_assessment']}"
            )
        if detail.get("reading_order"):
            console.print("\n[bold]Reading Order[/bold]")
            for i, item in enumerate(detail["reading_order"], 1):
                console.print(
                    f"  {i}. {item.get('citekey_or_ref', '?')} \u2014 {item.get('reason', '')}"
                )

    # Audit findings
    if report.audit_findings:
        console.print("\n[bold]Audit Findings[/bold]")
        for finding in report.audit_findings:
            severity = finding.get("severity", "medium")
            style = {"high": "red", "medium": "yellow", "low": "dim"}.get(
                severity, "white"
            )
            console.print(
                f"  [{style}]{severity.upper()}[/{style}] [{finding.get('type', '')}] {finding.get('details', '')}"
            )

    # Author network (audit mode)
    if audit:
        author_groups = state.get_key_author_groups(min_papers=2)
        if author_groups:
            console.print(
                "\n[bold]Key Author Groups[/bold] [dim](2+ papers in citation graph)[/dim]"
            )
            at = Table(show_edge=False, pad_edge=False)
            at.add_column("Author", style="cyan", max_width=20)
            at.add_column("Papers", justify="right", width=7)
            at.add_column("In Library", justify="right", width=10)
            for group in author_groups[:10]:
                at.add_row(
                    group["surname"],
                    str(group["paper_count"]),
                    str(group["in_library_count"]),
                )
            console.print(at)

    # Prune recommendations (auto-triggered when >100 sources)
    if report.prune:
        prune = report.prune
        drop = prune.get("drop", [])
        maybe = prune.get("maybe", [])
        total = state.get_library_summary().get("total", 0)
        after = total - len(drop)
        src_lookup = {s["id"]: s for s in state.get_all_sources()}

        console.print(
            f"\n[bold yellow]Prune Analysis[/bold yellow] [dim]({total} \u2192 ~{after} sources)[/dim]"
        )

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
    _print_ref_gaps_table(state, embeddings=kctx.embeddings)

    if kctx.project_root:
        console.print("\n[dim]Full report saved to notes/library/[/dim]")
    else:
        console.print("\n[dim]Full report saved to vault.[/dim]")


main.add_command(library)


@library.command()
@click.option("-c", "--chapter", type=int, help="Filter by chapter number")
@click.option(
    "-v", "--verdict", type=click.Choice(["drop", "maybe"]), help="Filter by verdict"
)
@click.option("--clear", "clear_key", help="Clear verdict for a citekey")
@click.option("--apply", is_flag=True, help="Delete all 'drop' sources from DB")
@click.option(
    "--yes", "-y", is_flag=True, help="Skip confirmation prompt (use with --apply)"
)
@click.pass_context
def prune(ctx, chapter, verdict, clear_key, apply, yes):
    """Browse and manage prune verdicts from library analysis."""
    kctx = _get_context(ctx)
    state = kctx.state

    if apply:
        verdicts = state.get_prune_verdicts(verdict="drop")
        if not verdicts:
            console.print("[dim]No 'drop' verdicts to apply.[/dim]")
            return
        console.print(
            f"\n[bold]Review {len(verdicts)} sources marked for removal[/bold]"
        )
        console.print(
            "[dim]y = delete, n = skip, q = quit. Vault notes are preserved.[/dim]\n"
        )
        deleted, skipped = 0, 0
        for i, item in enumerate(verdicts, 1):
            citekey = item["source_id"]
            src = state.get_source(citekey)
            title = (src.get("title") or "untitled") if src else "unknown"
            authors = (src.get("authors") or "") if src else ""
            year = src.get("year") if src else None
            abstract = (src.get("abstract") or "") if src else ""
            reason = item.get("reason") or ""
            q_score = item.get("quality_score")
            frag_count = item.get("fragment_count") or 0
            sections = item.get("sections") or ""
            # Header
            console.print(
                f"[bold red]\u2500\u2500 [{i}/{len(verdicts)}] @{citekey} \u2500\u2500[/bold red]"
            )
            console.print(f"  [bold]{title}[/bold]")
            if authors:
                console.print(f"  {authors}" + (f", {year}" if year else ""))
            console.print(
                f"  [dim]Quality: {q_score or '?'} | Fragments: {frag_count} | Sections: {sections or 'none'}[/dim]"
            )
            if reason:
                console.print(f"  [yellow]Reason: {reason}[/yellow]")
            if abstract:
                console.print(
                    f"  [dim]{abstract[:200]}{'...' if len(abstract) > 200 else ''}[/dim]"
                )
            if yes:
                state.delete_source(citekey)
                deleted += 1
                console.print("  [red]Deleted[/red]")
            else:
                choice = click.prompt(
                    "  Delete?", type=click.Choice(["y", "n", "q"]), default="n"
                )
                if choice == "q":
                    console.print("[dim]Quit.[/dim]")
                    break
                elif choice == "y":
                    state.delete_source(citekey)
                    deleted += 1
                    console.print("  [red]Deleted[/red]")
                else:
                    skipped += 1
                    console.print("  [green]Kept[/green]")
            console.print()
        console.print(
            f"[bold]Result: {deleted} deleted, {skipped} kept (vault notes preserved).[/bold]"
        )
        return

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
        show_edge=False,
        pad_edge=False,
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
    console.print(
        f"\n[dim]Total: {summary['drop']} drop, {summary['maybe']} maybe[/dim]"
    )


@library.command()
@click.pass_context
def duplicates(ctx):
    """Detect duplicate sources by metadata (DOI, author+year+title, title prefix)."""
    from ..skills.duplicate_checker import find_duplicates

    kctx = _get_context(ctx)
    sources = kctx.state.get_all_sources_metadata()

    if not sources:
        console.print("[dim]No sources in library.[/dim]")
        return

    pairs = find_duplicates(sources)

    if not pairs:
        console.print(
            f"[green]No duplicates found among {len(sources)} sources.[/green]"
        )
        return

    table = Table(
        title=f"Potential Duplicates ({len(pairs)} pairs)",
        show_edge=False,
        pad_edge=False,
    )
    table.add_column("#", width=4, style="dim")
    table.add_column("Source A", max_width=30)
    table.add_column("Source B", max_width=30)
    table.add_column("Strategy", width=18)
    table.add_column("Conf", width=5, justify="right")
    table.add_column("Detail", max_width=50)

    for i, pair in enumerate(pairs, 1):
        conf_style = "red" if pair.confidence >= 0.9 else "yellow"
        table.add_row(
            str(i),
            f"@{pair.citekey_a}",
            f"@{pair.citekey_b}",
            pair.strategy,
            f"[{conf_style}]{pair.confidence:.1f}[/{conf_style}]",
            pair.detail,
        )

    console.print(table)
    console.print(
        f"\n[yellow]{len(pairs)} potential duplicate pair(s) found "
        f"among {len(sources)} sources.[/yellow]"
    )
    console.print("[dim]Review and manually remove duplicates from Zotero.[/dim]")
