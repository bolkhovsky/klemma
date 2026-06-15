"""Draft group and outline commands."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click
from rich.panel import Panel
from rich.table import Table

from ..cli import (
    _coach_section_hint,
    _get_context,
    _init_ai,
    _show_writing_order,
    _sync_sections,
    console,
    main,
)


def _resolve_output(
    output: Optional[str],
    project_root: Optional[Path],
    section: str,
    no_save: bool,
) -> Optional[Path]:
    """Resolve output path for draft -s.

    Returns None if draft should be printed (no_save=True or no project_root).
    -o path overrides default notes/drafts/ location.
    --no-save takes priority over -o.
    """
    if no_save:
        return None
    if output:
        return Path(output).expanduser()
    if project_root:
        return project_root / "notes" / "drafts" / f"Draft_{section}.md"
    return None


@main.group(invoke_without_command=True)
@click.option(
    "--section",
    "-s",
    default=None,
    help="Section ID (e.g. 1.3.2) \u2014 standalone section draft mode",
)
@click.option("--model", default=None, help="Override AI model")
@click.option("--no-save", is_flag=True, help="Print draft without saving to file")
@click.option(
    "--output",
    "-o",
    default=None,
    help="Write draft to this path instead of default notes/drafts/",
)
@click.option(
    "--no-rag",
    is_flag=True,
    help="Skip per-block RAG retrieval (use section-level fragments only)",
)
@click.option(
    "-p",
    "--prompt",
    default="",
    help="Custom directive for AI (e.g. 'rely on goessling2016 and previous_paper.md')",
)
@click.option(
    "--verify-citations/--no-verify-citations",
    default=None,
    help="Run citation integrity check after draft (default: on per config)",
)
@click.pass_context
def draft(ctx, section, model, no_save, output, no_rag, prompt, verify_citations):
    """Generate dissertation section drafts.

    Standalone mode: klemma draft -s 1.3.2
    Subcommand mode: klemma draft introduction
    """
    if ctx.invoked_subcommand is not None:
        return

    if not section:
        click.echo(ctx.get_help())
        return

    # Standalone section draft mode

    from ..config import parse_chapter_from_section
    from ..skills.context_loader import (
        extract_previous_section_ending,
        extract_section,
        fit_prompt_budget,
        load_chapter_draft,
        load_outline_context,
        load_research_report,
        load_section_sources,
        parse_argument_blocks,
        retrieve_rag_fragments_per_block,
    )
    from ..skills.drafter import generate_draft

    kctx = _get_context(ctx)
    cfg = kctx.config
    _sync_sections(kctx)

    # Coach hint (informational, before AI call)
    hint = _coach_section_hint(kctx.state, section, kctx.project_root)
    if hint:
        console.print(f"[dim]\U0001f4a1 {hint}[/dim]")

    if model:
        cfg.ai.model = model
    ai = _init_ai(cfg)

    chapter = parse_chapter_from_section(section)
    if chapter is None:
        console.print(f"[red]Cannot determine chapter from section '{section}'[/red]")
        raise SystemExit(1)

    # 0. Show writing order context (Kallestinova 2011 results-first)
    _show_writing_order(kctx, section)

    # 0b. Load outline context (section title, descriptions, scientific contributions)
    outline_ctx: dict = {}
    section_title = ""
    if kctx.project_root:
        outline_ctx = load_outline_context(section, kctx.project_root)
        section_title = outline_ctx.get("section_title", "")

    # 1. Load research report
    research_report_content = ""
    if kctx.project_root:
        research_report_content = load_research_report(section, kctx.project_root) or ""
    if research_report_content:
        console.print(f"[dim]Research report found for {section}[/dim]")

    # 2. Load chapter draft + extract section
    existing_draft = ""
    draft_content = load_chapter_draft(
        chapter,
        cfg,
        kctx.vault,
        project=kctx.project,
        project_root=kctx.project_root,
    )
    if draft_content:
        existing_draft = extract_section(draft_content, section) or ""
    if existing_draft:
        console.print(
            f"[dim]Existing draft found ({len(existing_draft.split())} words)[/dim]"
        )

    # 2b. Extract previous section ending for continuity bridge
    prev_ending = ""
    if draft_content and kctx.project_root:
        prev_ending = extract_previous_section_ending(draft_content, section, max_chars=500)
    if not prev_ending and chapter > 1 and kctx.project_root:
        # Cross-chapter: load previous chapter draft
        prev_chapter_content = load_chapter_draft(
            chapter - 1, cfg, kctx.vault,
            project=kctx.project, project_root=kctx.project_root,
        )
        if prev_chapter_content:
            paras = [p.strip() for p in prev_chapter_content.split("\n\n") if p.strip()]
            if paras:
                prev_ending = paras[-1][:500]
    if prev_ending:
        console.print("[dim]Previous section ending loaded for continuity[/dim]")

    # 3. Load source summaries
    source_summaries = load_section_sources(section, chapter, kctx.state, kctx.vault)

    # 4. Per-block RAG fragments (He et al. 2010)
    rag_fragments_for_prompt = None
    if not no_rag and kctx.embeddings and research_report_content:
        argument_blocks = parse_argument_blocks(research_report_content)
        if argument_blocks:
            rag_fragments_for_prompt = retrieve_rag_fragments_per_block(
                argument_blocks,
                kctx.embeddings,
                kctx.state,
                top_k=5,
            )
            if rag_fragments_for_prompt:
                block_count = len(rag_fragments_for_prompt)
                frag_count = sum(
                    len(b.get("fragments", [])) for b in rag_fragments_for_prompt
                )
                console.print(
                    f"[dim]RAG: {frag_count} fragments across {block_count} argument blocks[/dim]"
                )

    # 4b. Section-level fragments (fallback / supplementary)
    fragments_raw = []
    # Collect RAG fragment IDs to avoid duplicates in section-level fallback
    rag_frag_sources = set()
    if rag_fragments_for_prompt:
        for block in rag_fragments_for_prompt:
            for f in block.get("fragments", []):
                rag_frag_sources.add((f.get("source", ""), f.get("text", "")[:50]))

    if kctx.embeddings and existing_draft:
        try:
            query_vec = kctx.embeddings.embed(existing_draft[:500])
            if query_vec:
                fragments_raw = kctx.state.retrieve_similar_fragments(
                    query_vec,
                    top_k=30,
                    model=kctx.embeddings.model_name,
                )
        except Exception:
            pass
    if len(fragments_raw) < 10:
        fallback = kctx.state.get_fragments(section=section, limit=30)
        seen_ids = {f["id"] for f in fragments_raw}
        for ff in fallback:
            if ff["id"] not in seen_ids:
                fragments_raw.append(ff)
                seen_ids.add(ff["id"])

    # Format fragments for prompt (exclude those already in RAG blocks)
    formatted_fragments = []
    for f in fragments_raw[:30]:
        source_key = (
            f.get("citekey", f.get("source_id", "?")),
            f.get("fragment_text", "")[:50],
        )
        if source_key in rag_frag_sources:
            continue
        formatted_fragments.append(
            {
                "source": f.get("citekey", f.get("source_id", "?")),
                "text": f.get("fragment_text", "")[:300],
                "type": f.get("fragment_type", "?"),
                "relevance": f.get("relevance_score", 3),
                "page": f.get("page_number"),
                "intent": f.get("citation_intent"),
                "verbatim": bool(f.get("verbatim", 0)),
            }
        )

    # Format sources for prompt
    formatted_sources = [
        {
            "citekey": src["id"],
            "quality": src.get("quality_score", 0),
            "priority": src.get("citation_priority", "medium"),
            "summary": src.get("vault_summary", ""),
        }
        for src in source_summaries
    ]

    # 5. Budget control
    draft_for_budget = draft_content[:30_000] if draft_content else ""
    (
        draft_for_budget,
        formatted_sources,
        formatted_fragments,
        rag_fragments_for_prompt,
    ) = fit_prompt_budget(
        draft_for_budget,
        formatted_sources,
        formatted_fragments,
        rag_fragments=rag_fragments_for_prompt,
    )

    # 6. Valid citekeys for hallucination filter
    valid_citekeys = kctx.state.get_existing_source_ids()

    # 7. Generate draft
    with console.status(f"Генерация черновика раздела {section}...", spinner="dots"):
        result = generate_draft(
            section,
            chapter,
            cfg,
            ai,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
            project_chain=kctx.project_chain,
            research_report_content=research_report_content,
            existing_draft=existing_draft,
            source_summaries=formatted_sources,
            fragments=formatted_fragments,
            rag_fragments=rag_fragments_for_prompt or [],
            valid_citekeys=valid_citekeys,
            section_title=section_title,
            custom_prompt=prompt,
            prev_ending=prev_ending,
            outline_context=outline_ctx or None,
        )

    if not result.text:
        console.print("[red]AI returned empty result.[/red]")
        raise SystemExit(1)

    # 7b. Citation integrity check (writer/verifier-split, ADR-018)
    draft_to_save = result.text
    _should_verify = verify_citations if verify_citations is not None else cfg.ai.verify_citations_inline
    if _should_verify:
        from ..skills.citation_checker import build_judge_provider, check_draft_inline
        with console.status("Проверка цитирований...", spinner="dots"):
            _judge = build_judge_provider(cfg)
            draft_to_save, _report = check_draft_inline(
                result.text,
                formatted_fragments,
                rag_fragments_for_prompt or [],
                config=cfg,
                judge_ai=_judge,
                project_root=kctx.project_root or Path("."),
                klemma_home=kctx.klemma_home,
                project_chain=kctx.project_chain,
                use_ai=_judge is not None,
            )
        warn_count = sum(
            1 for v in _report.verdicts if v.severity in ("soft_warn", "hard_warn")
        )
        if warn_count:
            console.print(
                f"[yellow]Цитирования: {warn_count} потенциально необоснованных утверждений "
                f"({_report.summary})[/yellow]"
            )
        if _report.status == "error":
            console.print("[red]Верификатор цитирований завершился с ошибкой[/red]")

    # 8. Save draft
    out_path = _resolve_output(output, kctx.project_root, section, no_save)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(draft_to_save, encoding="utf-8")
        console.print(f"[green]Saved to {out_path}[/green]")
    else:
        console.print(draft_to_save)

    # 9. Summary
    console.print(
        f"[bold]{result.word_count}[/bold] words, "
        f"[bold]{len(result.citations_used)}[/bold] citations"
    )
    if result.research_report_used:
        console.print("[dim]Based on research report[/dim]")
    if result.filtered_citekeys:
        console.print(
            f"[yellow]Filtered {len(result.filtered_citekeys)} hallucinated "
            f"citekeys: {', '.join(result.filtered_citekeys)}[/yellow]"
        )


main.add_command(draft)


@draft.command()
@click.option(
    "--section",
    "-s",
    default=None,
    help="Single section (e.g. актуальность, цель, задачи)",
)
@click.option("--output", "-o", default=None, help="Output file path")
@click.option("--model", default=None, help="Override AI model")
@click.pass_context
def introduction(ctx, section, output, model):
    """Generate introduction draft \u2014 12 mandatory ГОСТ sections."""
    from pathlib import Path

    kctx = _get_context(ctx)
    cfg = kctx.config
    if model:
        cfg.ai.model = model
    ai = _init_ai(cfg)

    from ..skills.introduction_drafter import GOST_SECTIONS, generate_introduction

    if section and section not in GOST_SECTIONS:
        console.print(f"[red]Unknown section '{section}'. Choose from:[/red]")
        for s in GOST_SECTIONS:
            console.print(f"  - {s}")
        raise SystemExit(1)

    with console.status("Генерация черновика введения...", spinner="dots"):
        result = generate_introduction(
            cfg,
            kctx.state,
            ai,
            project=kctx.project,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
            project_chain=kctx.project_chain,
            target_section=section,
        )

    if not result.text:
        console.print("[red]AI returned empty result.[/red]")
        raise SystemExit(1)

    # Save output
    if output:
        out_path = Path(output)
    else:
        suffix = f"_{section}" if section else ""
        out_path = kctx.project_root / f"\u0412\u0432\u0435\u0434\u0435\u043d\u0438\u0435{suffix}.md"

    out_path.write_text(result.text, encoding="utf-8")
    console.print(
        f"[green]Saved to {out_path}[/green] ({result.section_count} sections)"
    )


# --- Outline ---


@main.command()
@click.option("--no-save", is_flag=True, help="Show outline without saving")
@click.option(
    "--scan-only", is_flag=True, help="Show found files without AI generation"
)
@click.option(
    "-p",
    "--prompt",
    default="",
    help="Custom directive for AI (e.g. 'Focus on knowledge graph')",
)
@click.option(
    "--fresh", is_flag=True, help="Force full regeneration, ignore previous outline"
)
@click.pass_context
def outline(ctx, no_save, scan_only, prompt, fresh):
    """Generate project outline from directory contents + database context.

    Scans project files (.md, .tex, .bib), combines with library data,
    and uses AI to generate chapters, sections, and scientific results.

    On repeat runs, detects previous outline in project directory and runs incrementally.
    Use --fresh to regenerate from scratch.

    Examples:
      klemma outline
      klemma outline -p "Focus on knowledge graph representation"
      klemma outline --fresh
    """
    kctx = _get_context(ctx)
    cfg, state = kctx.config, kctx.state

    from ..config import scan_project_files
    from ..skills.outliner import generate_outline as gen_outline
    from ..skills.outliner import save_outline

    # Capture current section IDs before generation (for stale-assignment warning)
    old_sections = set(kctx.project.sections.keys()) if kctx.project else set()

    # 1. Scan project files
    project_files = scan_project_files(kctx.project_root)

    if not project_files:
        console.print("[yellow]No files found in project directory.[/yellow]")
        return

    # Show found files
    table = Table(title=f"Project files ({kctx.project_root.name})")
    table.add_column("File", style="cyan")
    table.add_column("Size", justify="right", width=8)
    for pf in project_files:
        size_str = (
            f"{pf['size']:,} B" if pf["size"] < 10000 else f"{pf['size'] // 1024} KB"
        )
        table.add_row(pf["path"], size_str)
    console.print(table)

    if scan_only:
        return

    # 2. AI generation
    ai = _init_ai(cfg)
    project_name = kctx.project_root.name

    spinner_msg = "Generating outline..."
    if fresh:
        spinner_msg = "Regenerating outline from scratch..."

    with console.status(spinner_msg, spinner="dots"):
        result, mode = gen_outline(
            cfg,
            state,
            ai,
            kctx.project_root,
            project_name=project_name,
            project=kctx.project,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
            custom_prompt=prompt,
            force_initial=fresh,
        )

    if not result.title:
        console.print("[red]Failed to generate outline.[/red]")
        return

    # 3. Show mode label
    if mode == "incremental":
        console.print("\n[dim]Mode: Incremental update[/dim]")
    elif fresh:
        console.print("\n[dim]Mode: Fresh regeneration[/dim]")
    else:
        console.print("\n[dim]Mode: Initial outline[/dim]")

    if prompt:
        console.print(f"[dim]Directive: {prompt}[/dim]")

    if result.update_summary:
        console.print(f"\n[green]> {result.update_summary}[/green]")

    # 4. Display outline
    console.print()
    console.print(
        Panel(
            f"[bold]{result.title}[/bold]\n\n{result.description}",
            title="Outline",
            border_style="blue",
        )
    )

    # Chapters + sections
    if result.chapters:
        console.print()
        table = Table(title="Structure")
        table.add_column("#", justify="right", width=5, style="dim")
        table.add_column("Title", max_width=50)
        table.add_column("Sections", max_width=40, style="cyan")

        for ch_num in sorted(result.chapters.keys()):
            ch_title = result.chapters[ch_num]
            ch_prefix = f"{ch_num}."
            ch_secs = [
                f"{k} {v}"
                for k, v in sorted(result.sections.items())
                if k.startswith(ch_prefix)
            ]
            table.add_row(
                str(ch_num),
                ch_title,
                "\n".join(ch_secs) if ch_secs else "",
            )
        console.print(table)

    # Scientific results
    if result.scientific_results:
        console.print("\n[green]Scientific Results:[/green]")
        for key, value in result.scientific_results.items():
            console.print(f"  [bold]{key}:[/bold] {value}")

    if no_save:
        return

    # 5. Save outline: to KLEMMA.md if present, else Outline_*.md (legacy)
    saved_path = save_outline(result, project_name, kctx.project_root)
    if saved_path.name == "KLEMMA.md":
        console.print("\n[dim]Outline saved to KLEMMA.md[/dim]")
    else:
        console.print(f"\n[dim]Saved: {saved_path}[/dim]")

    # 5b. Warn if section structure changed and sources may need reassignment
    if fresh and old_sections and result.sections:
        new_sections = set(result.sections.keys())
        removed = old_sections - new_sections
        if removed:
            console.print(
                f"\n[yellow]\u26a0 Section structure changed "
                f"(removed: {', '.join(sorted(removed))}).[/yellow]\n"
                "[yellow]  Sources may be assigned to outdated sections.[/yellow]\n"
                "[yellow]  Run: klemma reassign[/yellow]"
            )

    # 6. Auto-generate chapter_mapping from outline chapters
    if result.chapters:
        from ..config import generate_chapter_mapping, update_project_config

        mapping = generate_chapter_mapping(result.chapters, result.sections)
        if mapping:
            updates: dict = {
                "chapters": {str(k): v for k, v in result.chapters.items()},
                "chapter_mapping": [
                    {"pattern": m.pattern, "chapter": m.chapter, "section": m.section}
                    for m in mapping
                ],
            }
            update_project_config(kctx.project_root, updates)
            console.print(
                f"[green]Updated chapter_mapping ({len(mapping)} patterns) in config[/green]"
            )
