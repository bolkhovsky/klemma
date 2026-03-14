"""Management commands: init, info, tree, migrate, migrate_content, reassign, plan."""

from pathlib import Path

import click
from rich.panel import Panel
from rich.table import Table

from ..cli import (
    _discover_paper_sources,
    _get_context,
    _init_ai,
    _init_components,
    _interactive_init,
    _load_prefill,
    _print_project_tree,
    _sync_sections,
    console,
    main,
)
from ..config import (
    _load_yaml,
    discover_project_chain,
    discover_project_root,
    ensure_system_home,
)


@main.command()
@click.option(
    "--type",
    "-t",
    "project_type",
    default="dissertation",
    type=click.Choice(["dissertation", "paper", "thesis"]),
    help="Project type",
)
@click.option(
    "--global-only", is_flag=True, help="Only create/update ~/.klemma/ system config"
)
@click.option("--no-input", is_flag=True, help="Skip interactive prompts, use defaults")
@click.option("--non-interactive", is_flag=True, help="Alias for --no-input")
@click.option(
    "--force",
    is_flag=True,
    help="Re-run wizard even if project exists (prefills from current config)",
)
@click.option(
    "--outline", is_flag=True, help="Generate outline after init (requires AI)"
)
@click.option(
    "--plan",
    "plan_path",
    default=None,
    type=click.Path(exists=True),
    help="Path to dissertation plan-prospect .docx \u2014 auto-fills project from it",
)
@click.option(
    "--name", "project_name", default=None, help="Project title (non-interactive)"
)
@click.option(
    "--description", "-d", default=None, help="Project description (non-interactive)"
)
@click.option(
    "--keywords", "-k", default=None, help="Comma-separated keywords (non-interactive)"
)
@click.option(
    "--language", "-l", default=None, help="AI language: ru or en (non-interactive)"
)
@click.option(
    "--backend",
    "-b",
    default=None,
    type=click.Choice(["claude", "litellm"], case_sensitive=False),
    help="AI backend: claude (Claude Code Max) or litellm (non-interactive)",
)
@click.option(
    "--api-key", "api_key", default=None, help="OpenAI API key for litellm backend"
)
@click.pass_context
def init(
    ctx,
    project_type,
    global_only,
    no_input,
    non_interactive,
    force,
    outline,
    plan_path,
    project_name,
    description,
    keywords,
    language,
    backend,
    api_key,
):
    """Initialize a new klemma project in current directory.

    Creates .klemma/ and KLEMMA.md in the current directory.
    Also ensures ~/.klemma/ system config exists.

    Runs an interactive setup wizard by default. Use --no-input to skip prompts.
    Use --plan to initialize from a dissertation plan-prospect .docx file.

    \\b
    Examples:
      klemma init                                  # interactive setup
      klemma init --plan plan.docx                 # from dissertation plan
      klemma init --backend claude                 # Claude Code Max, S2 embeddings
      klemma init --backend litellm --api-key sk-  # OpenAI LLM + embeddings
      klemma init --force                          # re-run wizard
    """
    from ..setup import InitValues, init_project, init_system

    # --non-interactive is an alias for --no-input
    if non_interactive:
        no_input = True

    # If any value flags provided, auto-imply non-interactive mode
    has_value_flags = any(
        v is not None
        for v in [project_name, description, keywords, language, backend, api_key]
    )
    if has_value_flags:
        no_input = True

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

    # Ensure system directory exists (klemmarc updated after wizard collects values)
    init_system(system_home)

    project_dir = Path.cwd()
    config_path = project_dir / ".klemma" / "config.yaml"

    # Load existing config as prefill for --force
    prefill = None
    if force and config_path.exists():
        prefill = _load_prefill(config_path)
        # Remove existing files so init_project recreates them
        config_path.unlink()
        klemma_md = project_dir / "KLEMMA.md"
        if klemma_md.exists():
            klemma_md.unlink()

    # Check if config already exists (skip wizard unless --force)
    if not force and config_path.exists():
        no_input = True

    # --- --plan: initialize from dissertation plan-prospect .docx ---
    plan_data = None
    if plan_path:
        from ..plan_parser import parse as parse_plan

        try:
            plan_data = parse_plan(plan_path)
        except ImportError as e:
            console.print(f"[red]{e}[/red]")
            return
        except (FileNotFoundError, ValueError) as e:
            console.print(f"[red]Cannot parse plan: {e}[/red]")
            return

        console.print(f"\n[green]Parsed plan-prospect:[/green] {plan_path}")
        console.print(f"  Title: {plan_data.title[:80]}...")
        console.print(f"  Chapters: {len(plan_data.chapters)}")
        console.print(f"  Scientific results: {len(plan_data.results)}")
        console.print(f"  Research tasks: {len(plan_data.tasks)}")

        # Pre-fill from plan (user can still override via wizard)
        project_name = project_name or plan_data.title
        description = description or plan_data.description[:200]
        project_type = "dissertation"
        # Auto-enable outline
        outline = True

    values = None
    if plan_data and not no_input:
        # --plan mode: run wizard but pre-fill from plan, pass plan_data through
        prefill = prefill or {}
        prefill["title"] = plan_data.title
        prefill["description"] = plan_data.description[:200]
        prefill["project_type"] = "dissertation"
        prefill["_plan_data"] = plan_data
        values = _interactive_init(project_type, prefill=prefill)
        values.project_type = "dissertation"
        if not values.plan_data:
            values.plan_data = plan_data
    elif not no_input:
        values = _interactive_init(project_type, prefill=prefill)
        project_type = values.project_type
        # Plan may have been provided interactively
        if values.plan_data:
            plan_data = values.plan_data
            project_type = "dissertation"
            values.project_type = "dissertation"
            outline = True
    elif has_value_flags:
        # Build InitValues from CLI flags
        kw_list = (
            [k.strip() for k in keywords.split(",") if k.strip()] if keywords else []
        )
        # Derive model and embeddings from backend + api_key
        _ai_model = ""
        if backend == "claude":
            _ai_model = "sonnet"
        elif backend == "litellm":
            _ai_model = "openai/gpt-4.1"
        _emb = "openai" if api_key else ("s2" if backend == "claude" else "")
        values = InitValues(
            project_type=project_type,
            title=project_name or "",
            description=description or "",
            keywords=kw_list,
            language=language or "ru",
            backend=backend or "",
            ai_model=_ai_model,
            openai_api_key=api_key or "",
            embeddings_backend=_emb,
        )

    # Detect parent project before init -- affects config defaults (ADR-012)
    pre_chain = discover_project_chain(project_dir.parent)
    _has_parent = len(pre_chain) > 0

    result = init_project(
        project_dir, project_type=project_type, values=values, has_parent=_has_parent
    )

    # Overwrite KLEMMA.md with rich plan content if plan was provided
    effective_plan = plan_data or (values.plan_data if values else None)
    if effective_plan:
        from ..plan_parser import to_klemma_md as plan_to_klemma_md

        klemma_md_path = project_dir / "KLEMMA.md"
        klemma_md_path.write_text(plan_to_klemma_md(effective_plan), encoding="utf-8")
        console.print("  [green]+ KLEMMA.md (from plan-prospect)[/green]")

    # Update klemmarc with AI backend/keys from wizard
    if values and (values.backend or values.openai_api_key):
        sys_result = init_system(system_home, values=values)
        for name in sys_result.get("created", []):
            result.setdefault("created", []).append(name)

    if result["created"]:
        console.print(
            f"\n[green]Initialized klemma {project_type} project in {project_dir}/[/green]"
        )
        for name in result["created"]:
            console.print(f"  + {name}")
    if result["skipped"]:
        for name in result["skipped"]:
            console.print(f"  [dim]~ {name} (already exists, skipped)[/dim]")

    # Parent project detection: offer DB inheritance (#55)
    chain = discover_project_chain(project_dir)
    if len(chain) > 1:
        parent_root = chain[1]
        console.print(f"\n[cyan]Parent project detected at {parent_root}.[/cyan]")
        if not no_input:
            inherit = click.confirm("Inherit parent library?", default=True)
        else:
            inherit = True
        if not inherit:
            from ..config import update_project_config

            update_project_config(project_dir, {})  # ensure file exists
            # Write inherit_db: false to state section
            cfg_path = project_dir / ".klemma" / "config.yaml"
            raw = _load_yaml(cfg_path)
            raw.setdefault("state", {})["inherit_db"] = False
            import yaml

            cfg_path.write_text(
                yaml.dump(
                    raw, default_flow_style=False, allow_unicode=True, sort_keys=False
                ),
                encoding="utf-8",
            )
            console.print(
                "[dim]  inherit_db: false (parent library not inherited)[/dim]"
            )
        else:
            console.print(
                "[dim]  inherit_db: true (parent library will be inherited)[/dim]"
            )

    # Paper: discover relevant sources from vault + BBT JSON
    if (
        values
        and project_type == "paper"
        and (values.keywords or values.description)
        and values.vault_path
        and values.zotero_library_json
    ):
        _discover_paper_sources(project_dir, values)

    if outline:
        try:
            kctx = _init_components(ctx.obj["config_path"])
        except Exception as e:
            console.print(f"[yellow]Skipping outline: {e}[/yellow]")
        else:
            from ..config import scan_project_files
            from ..skills.outliner import generate_outline as gen_outline
            from ..skills.outliner import save_outline

            project_files = scan_project_files(kctx.project_root)
            if not project_files:
                console.print(
                    "[yellow]No files found in project directory; skipping outline.[/yellow]"
                )
            else:
                try:
                    ai = _init_ai(kctx.config)
                except Exception as e:
                    console.print(
                        "[yellow]Skipping outline: AI backend not configured.[/yellow]"
                    )
                    console.print(f"[dim]{e}[/dim]")
                else:
                    with console.status("Generating outline...", spinner="dots"):
                        result, _mode = gen_outline(
                            kctx.config,
                            kctx.state,
                            ai,
                            kctx.project_root,
                            project_name=kctx.project_root.name,
                            project=kctx.project,
                            dissertation_context=kctx.dissertation_context,
                            klemma_home=kctx.klemma_home,
                        )
                    if not result.title:
                        console.print("[red]Failed to generate outline.[/red]")
                    else:
                        saved_path = save_outline(
                            result, kctx.project_root.name, kctx.project_root
                        )
                        console.print(f"[dim]Outline saved: {saved_path}[/dim]")

    console.print()
    console.print("Run [bold]klemma status[/bold] to verify.")


@main.command()
@click.pass_context
def plan(ctx):
    """Daily plan \u2014 focus, recommendations, deadlines."""
    kctx = _get_context(ctx)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    ai = _init_ai(cfg)

    from ..skills.planner import generate_morning_plan

    with console.status("Генерация утреннего брифинга", spinner="dots"):
        plan_result = generate_morning_plan(
            cfg,
            state,
            vault,
            ai,
            project=kctx.project,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
        )

    console.print()

    # Status
    if plan_result.status_line:
        console.print(Panel(plan_result.status_line, border_style="blue"))

    # Intervention
    if plan_result.intervention and plan_result.intervention != "NONE":
        style = {
            "CELEBRATION": "green",
            "FOCUS_REDIRECT": "yellow",
            "ESCALATION": "red",
            "DEADLINE_RISK": "yellow",
            "DEADLINE_CRITICAL": "red bold",
        }.get(plan_result.intervention, "yellow")
        console.print(f"[{style}]{plan_result.intervention}[/{style}]")

    # Focus
    console.print(
        Panel(
            f"[bold]{plan_result.focus}[/bold]\n\n"
            f"[dim]Почему:[/dim] {plan_result.why}",
            title="Фокус сегодня",
            border_style="green",
        )
    )

    # Sources
    if plan_result.sources_needed:
        console.print(
            f"\n[cyan]Источники:[/cyan] {', '.join(plan_result.sources_needed)}"
        )

    # Assistant task
    if plan_result.assistant_task:
        console.print(
            f"\n[blue]Задача ассистента:[/blue] {plan_result.assistant_task}"
        )

    # Reading
    if plan_result.reading_target:
        console.print(f"\n[dim]Чтение:[/dim] {plan_result.reading_target}")

    # Strategy suggestions
    if plan_result.strategy_suggestions:
        console.print("\n[yellow]Предложения по стратегии:[/yellow]")
        for s in plan_result.strategy_suggestions:
            console.print(f"  - {s}")

    # Progress
    if plan_result.progress_summary:
        console.print(f"\n[dim]{plan_result.progress_summary}[/dim]")

    # Write briefing to daily note
    daily_content = f"## Klemma Брифинг\n\n{plan_result.briefing_text}\n"
    vault.append_to_daily(daily_content)
    console.print("\n[dim]Брифинг добавлен в daily note.[/dim]")


@main.command()
@click.pass_context
def info(ctx):
    """Show current project info: root, parent chain, config, DB."""
    project_chain = discover_project_chain()
    if not project_chain and not ctx.obj["config_path"]:
        console.print("[yellow]Not in a klemma project.[/yellow]")
        console.print("[dim]Run 'klemma init' to create a project here.[/dim]")
        return

    try:
        kctx = _get_context(ctx)
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

    console.print(
        Panel(
            "\n".join(info_parts),
            title=f"Project: {kctx.project_name}",
            border_style="blue",
        )
    )

    # Effective Zotero config (merged from system + parent + project)
    zot = kctx.config.zotero
    zot_parts = []
    if zot.library_json:
        zot_parts.append(f"BBT JSON: {zot.library_json}")
    if zot.storage_path:
        zot_parts.append(f"Storage: {zot.storage_path}")
    if zot_parts:
        console.print(
            Panel(
                "\n".join(zot_parts),
                title="Zotero (effective)",
                border_style="dim",
            )
        )

    # Parent chain
    if len(kctx.project_chain) > 1:
        console.print("\n[bold]Project Chain[/bold] (child \u2192 parent):")
        for i, root in enumerate(kctx.project_chain):
            marker = "[green]\u25cf[/green]" if i == 0 else "[dim]\u25cb[/dim]"
            console.print(f"  {marker} {root.name} ({root})")

    # Chapter structure
    if project.chapters:
        table = Table(title="Structure", show_edge=False, pad_edge=False)
        table.add_column("Ch", width=4, style="cyan")
        table.add_column("Title")
        for ch_num in sorted(project.chapters.keys()):
            table.add_row(str(ch_num), project.chapters[ch_num])
        console.print(table)

    # File conventions
    draft_pattern = (
        project.chapter_draft_pattern
        if project
        else kctx.config.dissertation.chapter_draft_pattern
    )
    conv_parts = [
        f"[bold]Chapter drafts[/bold]: {draft_pattern.format(chapter='N')}.md / .tex",
        f"  Location: [cyan]{project_root}/[/cyan] (vault as fallback)",
        f"[bold]Research notes[/bold]: {project_root}/notes/research/",
        f"[bold]Library reports[/bold]: {project_root}/notes/library/",
    ]
    if kctx.config.obsidian.vault_path:
        nf = kctx.config.obsidian.notes_folder
        tf = kctx.config.obsidian.tags_folder
        nf_ok = kctx.vault.check_folder(nf)
        tf_ok = kctx.vault.check_folder(tf)
        nf_s = "[green]\u2713[/green]" if nf_ok else "[red]\u2717 not found[/red]"
        tf_s = "[green]\u2713[/green]" if tf_ok else "[red]\u2717 not found[/red]"
        conv_parts.append(f"[bold]Reference notes[/bold]: vault {nf}/ {nf_s}")
        conv_parts.append(f"[bold]Tags[/bold]: vault {tf}/ {tf_s}")
    console.print(
        Panel(
            "\n".join(conv_parts),
            title="File Conventions",
            border_style="dim",
        )
    )


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


@main.command()
@click.argument("citekey", required=False, default=None)
@click.option(
    "--section",
    "-s",
    "target_section",
    default=None,
    help="Target section for direct reassignment (requires CITEKEY + --apply)",
)
@click.option(
    "--threshold",
    "-t",
    type=float,
    default=0.5,
    help="Minimum cosine similarity for suggestion (default: 0.5)",
)
@click.option(
    "--min-delta",
    type=float,
    default=0.0,
    help="Minimum score delta required to suggest reassignment (default: 0.0)",
)
@click.option(
    "--cross-type-penalty",
    type=float,
    default=0.05,
    help="Score penalty for cross-section-type suggestions (default: 0.05)",
)
@click.option(
    "--limit", "-n", type=int, default=20, help="Max suggestions to show (default: 20)"
)
@click.option(
    "--apply",
    is_flag=True,
    default=False,
    help="Apply reassignment (requires CITEKEY and -s SECTION)",
)
@click.pass_context
def reassign(ctx, citekey, target_section, threshold, min_delta, cross_type_penalty, limit, apply):
    """Suggest fragment-to-section reassignments based on embedding similarity.

    Optionally provide CITEKEY to filter suggestions for a single source.
    Combine with -s SECTION and --apply to directly reassign.
    """
    from ..embeddings import cosine_similarity

    kctx = _get_context(ctx)
    state = kctx.state
    _sync_sections(kctx)

    # Validate -s requires citekey
    if target_section and not citekey:
        console.print("[red]--section/-s requires a CITEKEY argument.[/red]")
        raise SystemExit(1)

    # Validate --apply requires citekey + section
    if apply and not (citekey and target_section):
        console.print(
            "[red]--apply requires both CITEKEY and -s SECTION.[/red]\n"
            "[dim]Example: klemma reassign myPaper2024 -s 1.1 --apply[/dim]"
        )
        raise SystemExit(1)

    # 1. Load section embeddings
    section_embeddings = state.get_all_section_embeddings()
    if not section_embeddings:
        console.print(
            "[red]No section embeddings found. "
            "Run 'klemma embed sections' first.[/red]"
        )
        raise SystemExit(1)

    # 2. Load fragment embeddings + metadata (including citation_intent + section_type)
    frag_embeddings = state.get_fragment_embeddings()
    if not frag_embeddings:
        console.print(
            "[red]No fragment embeddings found. "
            "Run 'klemma embed fragments' first.[/red]"
        )
        raise SystemExit(1)

    frag_meta = state.get_embedded_fragment_metadata()
    meta_by_id = {m["id"]: m for m in frag_meta}

    # 2b. Load section type map: {section_id: section_type_str}
    section_type_map: dict[str, str] = {}
    with state._conn() as _conn:
        for row in _conn.execute(
            "SELECT section, section_type FROM section_type_map"
        ).fetchall():
            section_type_map[row[0]] = row[1]

    # 2c. Load citation_intent per fragment: {frag_id: intent_str}
    frag_intents: dict[int, str] = {}
    with state._conn() as _conn:
        for row in _conn.execute(
            "SELECT id, citation_intent FROM fragments WHERE embedding IS NOT NULL"
        ).fetchall():
            if row[1]:
                frag_intents[row[0]] = row[1]

    # Intent → compatible section types (soft affinity)
    intent_type_affinity: dict[str, set[str]] = {
        "background": {"introduction", "literature_review", "background", "theoretical_framework"},
        "method": {"methodology", "implementation", "experiments"},
        "result_comparison": {"results", "discussion", "experiments"},
        "extends": {"discussion", "conclusion", "theoretical_framework"},
        "contrasts": {"discussion", "literature_review", "results"},
        "uses_data": {"data_description", "methodology", "experiments"},
    }
    intent_bonus = 0.03

    # Filter to active sources only (exclude orphaned fragments)
    active_sources = state.get_existing_source_ids()

    # Validate citekey exists
    if citekey and citekey not in active_sources:
        console.print(f"[red]Source @{citekey} not found in library.[/red]")
        raise SystemExit(1)

    # Build section name lookup from project config (chapter-level names)
    chapter_names: dict[str, str] = {}
    project = kctx.project
    if project:
        for num, title in (project.chapters or {}).items():
            chapter_names[str(num)] = title

    def _section_label(sec_id: str) -> str:
        """Resolve section ID to chapter name. '3.3' -> 'Гл. 3: <title>'."""
        if sec_id in chapter_names:
            return chapter_names[sec_id]
        chapter_num = sec_id.split(".")[0]
        name = chapter_names.get(chapter_num)
        if name:
            return f"\u0413\u043b. {chapter_num}"
        return ""

    # 3. Compute best section match for each fragment
    raw_suggestions = []

    with console.status(
        f"Computing affinity for {len(frag_embeddings)} fragments "
        f"\u00d7 {len(section_embeddings)} sections...",
        spinner="dots",
    ):
        for frag_id, frag_vec in frag_embeddings.items():
            meta = meta_by_id.get(frag_id)
            if not meta:
                continue
            source_id = meta.get("source_id", "")
            if source_id not in active_sources:
                continue
            current_section = meta.get("section") or ""

            # Score all sections
            scores = {}
            for sec_id, sec_vec in section_embeddings.items():
                scores[sec_id] = cosine_similarity(frag_vec, sec_vec)

            # Apply section-type penalty and citation-intent bonus
            current_stype = section_type_map.get(current_section, "")
            frag_intent = frag_intents.get(frag_id, "")
            intent_affinity = intent_type_affinity.get(frag_intent, set())

            adjusted: dict[str, float] = {}
            for sec_id, raw_score in scores.items():
                adj = raw_score
                sec_stype = section_type_map.get(sec_id, "")
                # Cross-type penalty
                if current_stype and sec_stype and sec_stype != current_stype:
                    adj -= cross_type_penalty
                # Intent-type bonus
                if sec_stype and sec_stype in intent_affinity:
                    adj += intent_bonus
                adjusted[sec_id] = adj

            ranked = sorted(adjusted.items(), key=lambda x: -x[1])
            best_section, best_score = ranked[0]
            current_score = adjusted.get(current_section, 0.0)
            delta = best_score - current_score

            # Only suggest if different from current, above threshold, and delta >= min_delta
            if (
                best_section
                and best_section != current_section
                and best_score >= threshold
                and delta >= min_delta
            ):
                raw_suggestions.append(
                    {
                        "frag_id": frag_id,
                        "citekey": source_id,
                        "current": current_section or "(none)",
                        "suggested": best_section,
                        "score": best_score,
                        "current_score": current_score,
                        "delta": delta,
                        "runner_up": ranked[1] if len(ranked) > 1 else None,
                        "preview": (meta.get("text_preview") or "")[:80],
                    }
                )

    # Filter by citekey if specified
    if citekey:
        raw_suggestions = [s for s in raw_suggestions if s["citekey"] == citekey]

    # Group by (citekey, current, suggested) -- collect all fragment IDs per group
    groups: dict[tuple[str, str, str], dict] = {}
    for s in raw_suggestions:
        key = (s["citekey"], s["current"], s["suggested"])
        if key not in groups:
            groups[key] = {**s, "frag_ids": [s["frag_id"]]}
        else:
            groups[key]["frag_ids"].append(s["frag_id"])
            if s["score"] > groups[key]["score"]:
                # Keep best-scoring fragment's metadata as representative
                best_frag_ids = groups[key]["frag_ids"]
                groups[key].update(s)
                groups[key]["frag_ids"] = best_frag_ids

    suggestions = list(groups.values())

    if not suggestions:
        console.print(
            f"[green]No reassignment suggestions above threshold {threshold:.2f} "
            f"among {len(frag_embeddings)} fragments.[/green]"
        )
        return

    # 4. Sort by score desc, limit
    suggestions.sort(key=lambda s: -s["score"])
    total = len(suggestions)
    suggestions = suggestions[:limit]

    for i, s in enumerate(suggestions, 1):
        cur_sec = s["current"]
        sug_sec = s["suggested"]
        cur_name = _section_label(cur_sec)
        sug_name = _section_label(sug_sec)
        delta = s["delta"]
        runner_up = s.get("runner_up")
        frag_count = len(s.get("frag_ids", [1]))

        console.print(f"[bold]\u2500\u2500 [{i}/{len(suggestions)}] @{s['citekey']} \u2500\u2500[/bold]")
        count_label = f" ({frag_count} fragments)" if frag_count > 1 else ""
        console.print(f"  [dim]Fragment:[/dim] {s['preview']}{count_label}")
        console.print(
            f"  [dim]Current:[/dim]   {cur_sec}"
            + (f" ({cur_name})" if cur_name else "")
            + f"  [dim]sim={s['current_score']:.3f}[/dim]"
        )
        score_style = "green" if s["score"] >= 0.7 else "yellow"
        console.print(
            f"  [bold]Suggested:[/bold] [{score_style}]{sug_sec}[/{score_style}]"
            + (f" ({sug_name})" if sug_name else "")
            + f"  [dim]sim={s['score']:.3f}[/dim]"
            + f"  [bold][{score_style}]+{delta:.3f}[/{score_style}][/bold]"
        )
        if runner_up:
            ru_sec, ru_score = runner_up
            ru_name = _section_label(ru_sec)
            console.print(
                f"  [dim]Runner-up: {ru_sec}"
                + (f" ({ru_name})" if ru_name else "")
                + f"  sim={ru_score:.3f}[/dim]"
            )
        console.print(
            f"  Run: [cyan]klemma reassign {s['citekey']} "
            f"-s {sug_sec} --apply[/cyan]"
        )
        console.print()

    if not apply:
        console.print(
            f"[dim]{total} suggestions total (showing top {len(suggestions)}). "
            f"Use the per-suggestion commands above to apply.[/dim]"
        )
        return

    # --- Direct apply: citekey + target_section bypasses interactive review ---
    if citekey and target_section:
        vault = kctx.vault
        notes_folder = kctx.config.obsidian.notes_folder

        # Only move fragments that embeddings suggest belong to target_section
        frag_ids_to_move: list[int] = []
        for s in suggestions:
            if s["citekey"] == citekey and s["suggested"] == target_section:
                frag_ids_to_move.extend(s.get("frag_ids", [s["frag_id"]]))

        if not frag_ids_to_move:
            # Check if there were suggestions for a different section
            other_suggestions = [
                s for s in suggestions
                if s["citekey"] == citekey and s["suggested"] != target_section
            ]
            if other_suggestions:
                best = other_suggestions[0]
                console.print(
                    f"[yellow]No suggestions match section {target_section} "
                    f"for @{citekey} (best match: {best['suggested']}, "
                    f"sim={best['score']:.3f}).[/yellow]"
                )
            else:
                console.print(
                    f"[yellow]No fragments for @{citekey} suggest "
                    f"section {target_section}.[/yellow]"
                )
            return

        console.print(
            f"Moving {len(frag_ids_to_move)} fragment(s) "
            f"for @{citekey} \u2192 {target_section}"
        )

        moved = 0
        for fid in frag_ids_to_move:
            ok = state.update_fragment_section(fid, target_section)
            if ok:
                moved += 1

        # Update vault frontmatter
        note_name = f"@{citekey}"
        props = vault.get_properties(note_name)
        vault_updated = False
        if props is not None:
            current = set(str(s) for s in props.get("sections", []))
            if target_section not in current:
                merged = current | {target_section}
                ok = vault.update_frontmatter_sections(
                    note_name, list(merged), folder=notes_folder,
                )
                if ok:
                    vault_updated = True

        console.print(
            f"[green]Reassigned {moved} fragment(s) for @{citekey} \u2192 {target_section}[/green]"
            + (" [dim](vault updated)[/dim]" if vault_updated else "")
        )
        return


# --- Migrate ---


@main.command()
@click.option(
    "--dry-run", is_flag=True, help="Preview changes without modifying anything"
)
@click.pass_context
def migrate(ctx, dry_run):
    """Migrate from ~/.klemma/ centralized config to per-directory project.

    Splits the old ~/.klemma/config.yaml into:
    - ~/.klemma/config.yaml (system: AI settings only)
    - .klemma/config.yaml (project: everything else)
    Also copies context.md \u2192 KLEMMA.md, tags.yaml, and DB.
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
        console.print(
            "[dim]~/.klemma/config.yaml looks like a system config already (no obsidian: section).[/dim]"
        )
        console.print("[dim]Just run 'klemma init' to create a project.[/dim]")
        return

    project_dir = Path.cwd()
    klemma_dir = project_dir / ".klemma"

    # Split config
    system_keys = {"ai", "mcp"}
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
        console.print("[bold]Dry run \u2014 no changes will be made:[/bold]\n")
        console.print(f"  Rewrite {old_config_path} \u2192 system config (ai only)")
        console.print(f"  Create  {klemma_dir / 'config.yaml'} \u2192 project config")
        for src, dst in copies:
            console.print(f"  Copy    {src} \u2192 {dst}")
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
        f.write("# Klemma global config \u2014 AI defaults\n")
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

    console.print("\n[green]Migration complete.[/green]")
    console.print(f"[dim]Project created in {project_dir}/[/dim]")
    console.print(f"[dim]System config at {system_home}/[/dim]")


# --- Three-tier library migration (ADR-014 Phase 1C) ---


def _table_exists(conn, name: str) -> bool:
    """Return True if a table exists in the SQLite connection."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


@main.command(name="migrate-library")
@click.option(
    "--apply",
    "do_run",
    is_flag=True,
    default=False,
    help="Actually run the migration (default: dry-run only)",
)
@click.pass_context
def migrate_library(ctx, do_run):
    """Migrate monolithic klemma.db → library.db + project.db (ADR-014).

    Reads sources, fragments, and embeddings from the monolithic klemma.db and
    populates:
      - ~/.klemma/library.db: papers, fragments, embeddings (shared across projects)
      - .klemma/data/project.db: source–section assignments for this project

    Default is dry-run (shows what would be migrated). Pass --apply to execute.
    Creates a backup at .klemma/data/klemma.db.bak before writing.
    """
    import shutil
    import sqlite3

    kctx = _get_context(ctx)
    mono_db = kctx.klemma_home / "data" / "klemma.db"

    if not mono_db.exists():
        console.print(f"[yellow]No monolithic DB found at {mono_db}[/yellow]")
        console.print("[dim]Nothing to migrate — already on the three-tier layout?[/dim]")
        return

    # ---- Read monolithic DB ------------------------------------------------
    conn = sqlite3.connect(str(mono_db))
    conn.row_factory = sqlite3.Row

    sources = conn.execute(
        "SELECT id, title, authors, year, abstract, doi, status, pdf_path, quality_score FROM sources"
    ).fetchall()
    fragments = conn.execute(
        "SELECT source_id, fragment_text, fragment_type, page_number, citation_intent FROM fragments"
    ).fetchall()
    sections = conn.execute(
        "SELECT source_id, section FROM source_sections"
    ).fetchall() if _table_exists(conn, "source_sections") else []
    conn.close()

    n_sources = len(sources)
    n_frags = len(fragments)
    n_secs = len(sections)

    _cfg = kctx.config
    if _cfg.library_db_path:
        _p = _cfg.library_db_path.expanduser()
        _lib_db_preview = _p if _p.is_absolute() else (kctx.system_home / _p)
    else:
        _lib_db_preview = kctx.system_home / "library.db"
    console.print(f"\n[bold]Three-tier library migration[/bold] — {'DRY RUN' if not do_run else 'LIVE'}")
    console.print(f"  Source DB   : {mono_db}")
    console.print(f"  library.db  : {_lib_db_preview}")
    console.print(f"  project.db  : {kctx.klemma_home / 'data' / 'project.db'}")
    console.print(f"\n  [cyan]{n_sources}[/cyan] sources · [cyan]{n_frags}[/cyan] fragments · [cyan]{n_secs}[/cyan] section assignments")

    if not do_run:
        console.print("\n[dim]Dry run — pass --apply to execute migration[/dim]")
        return

    # ---- Backup ------------------------------------------------------------
    bak = mono_db.with_suffix(".db.bak")
    shutil.copy2(mono_db, bak)
    console.print(f"\n[green]Backup created:[/green] {bak}")

    # ---- Migrate to library.db ---------------------------------------------
    from ..hashing import compute_content_hash, compute_prompt_hash
    from ..models import FragmentRecord
    from ..stores import LocalPaperStore, LocalUserLibrary

    # Use configured library_db_path if set, otherwise default to system_home/library.db
    cfg = kctx.config
    if cfg.library_db_path:
        _p = cfg.library_db_path.expanduser()
        _lib_db = _p if _p.is_absolute() else (kctx.system_home / _p)
    else:
        _lib_db = kctx.system_home / "library.db"
    paper_store = LocalPaperStore(_lib_db)
    user_lib = LocalUserLibrary(_lib_db)

    citekey_to_paper_id: dict[str, str] = {}
    migrated_papers = 0
    migrated_frags = 0

    # Build citekey → fragment list index
    frag_by_citekey: dict[str, list] = {}
    for f in fragments:
        frag_by_citekey.setdefault(f["source_id"], []).append(f)

    for src in sources:
        citekey = src["id"]
        pdf_hash = f"migrated:{citekey}"  # synthetic hash for migrated sources
        paper_id = paper_store.register_paper(
            title=src["title"] or citekey,
            authors=src["authors"] or "",
            year=src["year"],
            doi=src["doi"] or None,
            abstract=src["abstract"] or "",
            pdf_hash=pdf_hash,
        )
        citekey_to_paper_id[citekey] = paper_id
        migrated_papers += 1

        # Register in user library
        user_lib.add_source(
            paper_id,
            citekey,
            status=src["status"] or "pending",
            pdf_path=src["pdf_path"],
            quality_score=src["quality_score"],
        )

        # Migrate fragments
        ck_frags = frag_by_citekey.get(citekey, [])
        if ck_frags:
            records = [
                FragmentRecord(
                    fragment_id=compute_content_hash(paper_id, f["fragment_text"], f["page_number"]),
                    paper_id=paper_id,
                    fragment_text=f["fragment_text"],
                    fragment_type=f["fragment_type"] or "key_idea",
                    page_number=f["page_number"],
                    citation_intent=f["citation_intent"],
                    content_hash=compute_content_hash(paper_id, f["fragment_text"], f["page_number"]),
                )
                for f in ck_frags
            ]
            p_hash = compute_prompt_hash("migrated")
            migrated_frags += paper_store.save_fragments(paper_id, records, p_hash, "migrated")

    # ---- Migrate to project.db -------------------------------------------
    from ..stores import LocalProjectStore

    project_store = LocalProjectStore(kctx.klemma_home / "data" / "project.db")

    # Build section assignments
    secs_by_citekey: dict[str, list[str]] = {}
    for s in sections:
        secs_by_citekey.setdefault(s["source_id"], []).append(s["section"])

    for citekey, paper_id in citekey_to_paper_id.items():
        sec_list = secs_by_citekey.get(citekey, [])
        if sec_list:
            project_store.set_source_sections(citekey, paper_id, sec_list, [])

    console.print("\n[green]Migration complete:[/green]")
    console.print(f"  Papers registered : {migrated_papers}")
    console.print(f"  Fragments migrated: {migrated_frags}")
    console.print(f"  Section entries   : {sum(len(v) for v in secs_by_citekey.values())}")
    console.print(f"\n[dim]Monolithic DB preserved at {mono_db}[/dim]")
    console.print("[dim]Run 'klemma status' to verify coverage is unchanged.[/dim]")
    console.print(
        "\n[yellow]Note:[/yellow] Migrated papers use citekey-based deduplication "
        "(not PDF SHA256). Same paper under different citekeys in two projects will "
        "create separate entries. Re-process with [bold]klemma process --force[/bold] "
        "to upgrade to content-addressable dedup."
    )


# --- Migrate content fields to KLEMMA.md frontmatter ---


@main.command(name="migrate-content", hidden=True)
@click.option("--dry-run", is_flag=True, help="Preview changes without modifying files")
@click.pass_context
def migrate_content(ctx, dry_run):
    """[hidden] Migrate content fields from config.yaml to KLEMMA.md frontmatter.

    Moves chapters, scientific_results, title, deadlines, etc. from
    .klemma/config.yaml project: section into KLEMMA.md YAML frontmatter.
    Leaves infrastructure (ai, zotero, obsidian, state) in config.yaml.

    Run once per project after upgrading to the new KLEMMA.md format.
    """
    from ..setup import migrate_content_to_klemma_md

    kctx = _get_context(ctx)
    project_root = kctx.project_root

    if dry_run:
        console.print("[bold]Dry run \u2014 showing what would be migrated:[/bold]\n")
        import yaml as _yaml
        config_path = project_root / ".klemma" / "config.yaml"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                raw = _yaml.safe_load(f) or {}
            content_fields = {
                "type", "title", "description", "current_focus", "chapters",
                "scientific_results", "priority_terms", "chapter_mapping",
                "section_type_map", "deadlines", "writing_constraints",
                "min_sources_per_section", "auto_register",
            }
            project = raw.get("project", {})
            found = [k for k in content_fields if k in project]
            if found:
                console.print(f"Would migrate from config.yaml project: {found}")
            diss = raw.get("dissertation", {})
            if diss:
                console.print(f"Would migrate from config.yaml dissertation: {list(diss.keys())}")
            if not found and not diss:
                console.print("[dim]No content fields found to migrate.[/dim]")
        console.print(f"\n[dim]Target: {project_root / 'KLEMMA.md'}[/dim]")
        return

    result = migrate_content_to_klemma_md(project_root)
    migrated = result["migrated_fields"]
    warnings = result["warnings"]

    if migrated:
        console.print(f"[green]Migrated {len(migrated)} fields to KLEMMA.md:[/green]")
        for f in migrated:
            console.print(f"  [dim]+ {f}[/dim]")
    else:
        console.print("[yellow]No content fields found to migrate.[/yellow]")

    for w in warnings:
        console.print(f"[yellow]Warning: {w}[/yellow]")

    console.print(f"\n[dim]KLEMMA.md updated at {project_root / 'KLEMMA.md'}[/dim]")
    console.print("[dim]config.yaml stripped of content fields (infrastructure only)[/dim]")


# --- Migrate frontmatter ---


@main.command(name="migrate-frontmatter")
@click.option("--dry-run", is_flag=True, help="Preview changes without modifying files")
@click.pass_context
def migrate_frontmatter(ctx, dry_run):
    """Migrate vault notes from singular section:/chapter: to plural sections:/chapters:.

    Scans all @*.md notes in the Obsidian vault and rewrites frontmatter:
    - `section: "1.2"` → `sections: ["1.2"]` (merged with any existing sections list)
    - `chapter: 1` → `chapters: [1]` (merged with any existing chapters list)
    - Singular fields are removed after migration

    Idempotent: notes already using plural fields are skipped.
    """
    import yaml as _yaml

    kctx = _get_context(ctx)
    vault = kctx.vault
    config = kctx.config

    if vault is None:
        console.print("[red]No Obsidian vault configured. Set obsidian.vault_path.[/red]")
        raise SystemExit(1)

    notes_folder = config.obsidian.notes_folder or ""
    note_names = vault.list_notes(folder=notes_folder)
    citekey_notes = [n for n in note_names if n.startswith("@")]

    updated = 0
    skipped = 0

    for note_name in citekey_notes:
        props = vault.get_properties(note_name)
        if not props:
            skipped += 1
            continue

        singular_section = str(props.get("section", "")).strip() if props.get("section") else None
        singular_chapter = props.get("chapter")
        has_singular = bool(singular_section) or bool(singular_chapter)

        if not has_singular:
            skipped += 1
            continue

        # Build merged plural lists
        sections_list = list(props.get("sections") or [])
        chapters_list = list(props.get("chapters") or [])

        if singular_section and singular_section not in [str(s) for s in sections_list]:
            sections_list.append(singular_section)
        if singular_chapter is not None:
            try:
                ch = int(singular_chapter)
                if ch not in [int(c) for c in chapters_list]:
                    chapters_list.append(ch)
            except (TypeError, ValueError):
                pass

        if dry_run:
            console.print(f"[dim]{note_name}[/dim]: section={singular_section!r} → sections={sections_list}")
            updated += 1
            continue

        # Rewrite the note's frontmatter via vault adapter
        if notes_folder:
            target = vault._resolve_folder(notes_folder) / f"{note_name}.md"
        else:
            found = list(vault.vault_path.rglob(f"{note_name}.md"))
            target = found[0] if found else None

        if not target or not target.exists():
            skipped += 1
            continue

        text = target.read_text(encoding="utf-8")
        if not text.startswith("---"):
            skipped += 1
            continue
        end = text.find("---", 3)
        if end == -1:
            skipped += 1
            continue

        fm = _yaml.safe_load(text[3:end]) or {}
        fm["sections"] = sorted(
            [str(s) for s in sections_list],
            key=lambda s: [int(x) for x in s.split(".")] if all(x.isdigit() for x in s.split(".")) else [0],
        )
        fm["chapters"] = sorted(int(c) for c in chapters_list)
        fm.pop("section", None)
        fm.pop("chapter", None)

        new_fm = _yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
        target.write_text(f"---\n{new_fm}---{text[end + 3:]}", encoding="utf-8")
        updated += 1

    verb = "Would update" if dry_run else "Updated"
    console.print(f"[green]{verb} {updated} notes[/green], skipped {skipped} (already plural or no frontmatter).")


# --- Import (hidden) ---


@main.command(name="import", hidden=True)
@click.option(
    "--with-queue",
    is_flag=True,
    help="Also populate reading queue from high-priority sources",
)
@click.pass_context
def import_vault(ctx, with_queue):
    """Import/sync vault notes into klemma database.

    Scans @*.md files in the vault's notes folder, reads YAML frontmatter,
    and syncs source metadata and section assignments with the database.
    """
    kctx = _get_context(ctx)
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
            name = (
                project.chapters.get(ch, "")
                if project
                else cfg.dissertation.chapters.get(ch, "")
            )
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
            console.print(
                f"[blue]Reading queue: {queue_added} high-priority papers added.[/blue]"
            )


# --- Backward-compatible aliases ---


@main.command(hidden=True)
@click.pass_context
def morning(ctx):
    """[alias] -> plan"""
    ctx.invoke(plan)


@main.command(hidden=True)
@click.argument("citekey")
@click.pass_context
def extract(ctx, citekey):
    """[alias] -> process"""
    from .process import process as process_cmd

    ctx.invoke(process_cmd, citekeys=(citekey,))


@main.command(hidden=True)
@click.argument("query")
@click.option("--section", "-s", default=None)
@click.option("--chapter", "-ch", type=int, default=None)
@click.pass_context
def agent(ctx, query, section, chapter):
    """[alias] -> ask"""
    from .research import ask

    ctx.invoke(ask, query=query, section=section, chapter=chapter)


@main.command(hidden=True)
@click.option("--with-queue", is_flag=True)
@click.pass_context
def prepopulate(ctx, with_queue):
    """[alias] -> import"""
    ctx.invoke(import_vault, with_queue=with_queue)
