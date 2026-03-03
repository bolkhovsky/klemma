"""Klemma CLI — AI academic assistant."""

from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__, get_banner
from .ai import create_ai
from .config import (
    _load_yaml,
    discover_project_chain,
    discover_project_root,
    ensure_system_home,
    load_available_tags,
    load_project_context,
    resolve_effective_config,
)
from .context import KlemmaContext
from .embeddings import create_embeddings
from .library_provider import create_library
from .state import StateManager
from .vault import VaultAdapter

console = Console()


def _resolve_parent_db(parent_root: Path) -> Path | None:
    """Resolve parent project's DB path from its .klemma/config.yaml."""
    parent_config_path = parent_root / ".klemma" / "config.yaml"
    if not parent_config_path.exists():
        return None
    raw = _load_yaml(parent_config_path)
    db_rel = raw.get("state", {}).get("db_path", "./data/klemma.db")
    db_path = Path(db_rel)
    if not db_path.is_absolute():
        db_path = parent_root / ".klemma" / db_rel
    return db_path


def _init_components(config_path: str | None = None) -> KlemmaContext:
    """Initialize all components by discovering project from cwd.

    Uses Git-style .klemma/ discovery. If config_path is given, uses it directly.
    """
    system_home = ensure_system_home()

    # Git-style discovery: find .klemma/ by traversing up from cwd
    project_chain = discover_project_chain()

    if project_chain:
        # Discovered project — merge config with system defaults and inheritance
        cfg, project, project_root = resolve_effective_config(
            project_chain, config_override=config_path,
        )
        klemma_home = project_root / ".klemma"
    elif config_path:
        # No project found, but explicit --config given — use it with system defaults
        cfg, project, project_root = resolve_effective_config(
            [], config_override=config_path,
        )
        klemma_home = project_root / ".klemma"
        if not klemma_home.is_dir():
            klemma_home = system_home
        project_chain = [project_root]
    else:
        raise click.ClickException(
            "Not in a klemma project. Run 'klemma init' to create one here."
        )

    # Resolve db_path relative to project's .klemma/ directory
    db_path = cfg.state.db_path
    if not Path(db_path).is_absolute():
        db_path = str(klemma_home / db_path)

    state = StateManager(db_path)

    # Attach parent DB for read-only inheritance (#55)
    if len(project_chain) > 1 and cfg.state.inherit_db:
        parent_db = _resolve_parent_db(project_chain[1])
        if parent_db and parent_db.exists():
            state.set_parent(parent_db)

    vault = VaultAdapter(cfg.obsidian.vault_path, use_cli=cfg.obsidian.use_cli)
    library = create_library(cfg)

    # Embeddings: create provider if configured
    emb_cfg = cfg.embeddings
    emb_provider = None
    if emb_cfg.backend:
        emb_provider = create_embeddings(
            emb_cfg.model_dump(),
            api_keys=cfg.ai._resolved_api_keys or None,
        )

    dissertation_context = load_project_context(project_chain, cfg)
    available_tags = load_available_tags(klemma_home, cfg, project_chain=project_chain)

    return KlemmaContext(
        config=cfg, state=state, vault=vault, library=library,
        embeddings=emb_provider,
        project=project, project_name=project_root.name,
        klemma_home=klemma_home,
        dissertation_context=dissertation_context,
        available_tags=available_tags,
        project_root=project_root,
        project_chain=project_chain,
        system_home=system_home,
    )


def _get_context(ctx) -> KlemmaContext:
    """Get KlemmaContext from cache (set in main()) or initialize fresh."""
    if "kctx" in ctx.obj:
        return ctx.obj["kctx"]
    kctx = _init_components(ctx.obj["config_path"])
    ctx.obj["kctx"] = kctx
    return kctx


def _init_ai(cfg):
    """Initialize AI client (separate to allow commands without API key)."""
    return create_ai(cfg.ai)


BBTIndex = tuple[dict[str, str], dict[tuple[str, str], tuple[str, str]]]


def build_bbt_index(entry_lookup: dict) -> BBTIndex:
    """Build lookup indexes from BBT entries for orphan resolution.

    Returns (by_item_key, by_author_year):
      - by_item_key: {item_key: citekey}
      - by_author_year: {(author_lower, year): (citekey, item_key)}
    """
    import re

    by_item_key: dict[str, str] = {}
    by_author_year: dict[tuple[str, str], tuple[str, str]] = {}
    for ck, entry in entry_lookup.items():
        if entry.item_key:
            by_item_key[entry.item_key] = ck
        am = re.match(r"([a-z.]+?)(?=[A-Z\d])", ck)
        ym = re.search(r"(\d{4})", ck)
        if am and ym:
            author = am.group(1).replace(".", "").lower()
            by_author_year[(author, ym.group(1))] = (ck, entry.item_key or "")
    return by_item_key, by_author_year


def resolve_orphan(old_ck: str, bbt_index: BBTIndex) -> tuple[str, str] | None:
    """Try to match an orphan DB source to a BBT entry.

    Handles three citekey formats:
    1. Bare Zotero key (e.g. "5S6AH9KP") — matched via item_key lookup
    2. Acquire-format (e.g. "Lo2020_S2ORC_Title") — Author+year extraction
    3. BBT-format (e.g. "loTitle2020a") — lowercase author prefix extraction

    Returns (new_citekey, item_key) or None.
    """
    import re

    by_item_key, by_author_year = bbt_index

    # Strategy 1: bare Zotero key (8-char alphanumeric)
    if re.fullmatch(r"[A-Z0-9]{8}", old_ck):
        new_ck = by_item_key.get(old_ck)
        if new_ck:
            return (new_ck, old_ck)

    # Strategy 2: acquire-format "Author2020_Title_Slug"
    acq = re.match(r"([A-Z][a-z]+)(\d{4})", old_ck)
    if acq:
        match = by_author_year.get((acq.group(1).lower(), acq.group(2)))
        if match:
            return match

    # Strategy 3: BBT-format "authorTitle2022a"
    clean = re.sub(r"^[a-z]\.[a-z]\.", "", old_ck)
    am = re.match(r"([a-z.]+?)(?=[A-Z\d])", clean)
    ym = re.search(r"(\d{4})", old_ck)
    if am and ym:
        match = by_author_year.get((am.group(1).replace(".", "").lower(), ym.group(1)))
        if match:
            return match
    return None


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
    # Papers: explicit-only model — skip auto-registration from BBT JSON or vault
    project = ctx.project
    auto_register = not project or project.type != "paper"

    # Load existing DB source IDs (needed for paper filtering + new entry detection)
    existing = state.get_existing_source_ids()

    new_entries = []
    renames = []
    if ctx.library:
        entry_lookup = ctx.library.entries
        vault_citekeys = {vd["citekey"] for vd in vault_data}

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
            if auto_register and citekey not in vault_citekeys:
                classification = auto_classify(entry, cfg)
                new_entries.append((citekey, classification))

        # Fuzzy orphan cleanup: DB sources not in BBT JSON (pre-existing renames)
        bbt_citekeys = set(entry_lookup.keys())
        orphans = existing - bbt_citekeys
        if orphans:
            bbt_index = build_bbt_index(entry_lookup)
            for old_ck in list(orphans):
                result = resolve_orphan(old_ck, bbt_index)
                if not result:
                    continue
                new_ck, item_key = result
                if new_ck in existing:
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
    # Papers: only sync vault notes for sources already registered in DB
    if not auto_register:
        vault_data = [vd for vd in vault_data if vd["citekey"] in existing]

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


def _print_status_line(state: StateManager, project_name: str = "default", model: str = ""):
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
        if model:
            parts.insert(1 if project_name != "default" else 0, f"[magenta]{model}[/magenta]")
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


def _print_recommended_actions(
    proc_stats: dict,
    emb_stats: dict | None,
    gaps_data: list[dict],
    ref_gaps: list[dict],
    prune_summary: dict,
):
    """Print recommended next actions with copy-paste commands."""
    actions: list[tuple[str, str]] = []  # (reason, command)

    # 1. Pending/failed sources → process
    pending = proc_stats.get("pending", 0)
    failed = proc_stats.get("failed", 0)
    if pending > 0:
        actions.append((
            f"{pending} sources pending extraction",
            "klemma process",
        ))
    if failed > 0:
        actions.append((
            f"{failed} failed sources to retry",
            "klemma process --retry",
        ))

    # 2. Embedding coverage < 100%
    if emb_stats:
        total = emb_stats.get("total", 0)
        embedded = emb_stats.get("embedded", 0)
        remaining = total - embedded
        if remaining > 0:
            actions.append((
                f"{remaining} sources missing embeddings ({embedded}/{total})",
                "klemma embed",
            ))

    # 3. Top coverage gaps → research
    if gaps_data:
        top_gap = gaps_data[0]
        actions.append((
            f"section {top_gap['section']} has only {top_gap['count']} sources",
            f"klemma research -s {top_gap['section']}",
        ))

    # 4. Top ref gaps → acquire with pre-filled metadata flags
    for g in ref_gaps[:2]:
        authors = (g.get("ref_authors") or "").strip()
        year = g.get("ref_year") or ""
        title = (g.get("ref_title") or "").strip()
        flags = []
        if title:
            flags.append(f'-t "{title}"')
        if authors:
            flags.append(f'-a "{authors}"')
        if year:
            flags.append(f"-y {year}")
        flag_str = " ".join(flags)
        actions.append((
            f"missing ref: {authors[:30]} ({year}), cited x{g['count']}",
            f"klemma acquire <pdf_url> {flag_str}",
        ))

    # 5. Prune verdicts pending review
    if prune_summary.get("total", 0) > 0:
        drop = prune_summary.get("drop", 0)
        maybe = prune_summary.get("maybe", 0)
        actions.append((
            f"{drop} drop + {maybe} maybe prune verdicts pending",
            "klemma library prune --list",
        ))

    if not actions:
        return

    console.print()
    console.print("[bold]Recommended Actions[/bold]")
    for i, (reason, cmd) in enumerate(actions, 1):
        console.print(f"  [dim]{i}.[/dim] {reason}")
        console.print(f"     [green]$ {cmd}[/green]")


def _print_ref_gaps_table(state: StateManager, limit: int = 20, embeddings=None,
                          section_weights: dict[str, float] | None = None):
    """Print reference gaps as a Rich table.

    When embeddings is provided, applies semantic reranking via
    rerank_gaps_semantic() before display.
    """
    ref_gaps = state.get_reference_gaps(limit=limit, section_weights=section_weights)
    if not ref_gaps:
        return
    if embeddings:
        ref_gaps = state.rerank_gaps_semantic(ref_gaps, embeddings=embeddings)
    gap_summary = state.get_gap_summary()
    title_suffix = " [dim](semantically reranked)[/dim]" if embeddings else ""
    ref_table = Table(
        title=f"Reference Gaps — {gap_summary['open_count']} open (missing from library){title_suffix}",
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
    if ctx.invoked_subcommand in (None, "init"):
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

    if ctx.invoked_subcommand is not None and ctx.invoked_subcommand not in skip_check:
        # Initialize once and cache for subcommands to reuse
        try:
            kctx = _init_components(config)
            ctx.obj["kctx"] = kctx
            _print_status_line(kctx.state, project_name=kctx.project_name, model=kctx.config.ai.model)
        except Exception:
            pass

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command()
@click.option("--type", "-t", "project_type", default="dissertation",
              type=click.Choice(["dissertation", "paper", "thesis"]),
              help="Project type")
@click.option("--global-only", is_flag=True, help="Only create/update ~/.klemma/ system config")
@click.option("--no-input", is_flag=True, help="Skip interactive prompts, use defaults")
@click.option("--non-interactive", is_flag=True, help="Alias for --no-input")
@click.option("--force", is_flag=True, help="Re-run wizard even if project exists (prefills from current config)")
@click.option("--outline", is_flag=True, help="Generate outline after init (requires AI)")
@click.option("--name", "project_name", default=None, help="Project title (non-interactive)")
@click.option("--description", "-d", default=None, help="Project description (non-interactive)")
@click.option("--keywords", "-k", default=None, help="Comma-separated keywords (non-interactive)")
@click.option("--language", "-l", default=None, help="AI language: ru or en (non-interactive)")
@click.pass_context
def init(ctx, project_type, global_only, no_input, non_interactive, force, outline,
         project_name, description, keywords, language):
    """Initialize a new klemma project in current directory.

    Creates .klemma/ and KLEMMA.md in the current directory.
    Also ensures ~/.klemma/ system config exists.

    Runs an interactive setup wizard by default. Use --no-input to skip prompts.
    Pass --name, --description, --keywords, --language for non-interactive setup
    with custom values (auto-implies --no-input).

    \b
    Examples:
      klemma init                    # interactive setup
      klemma init --type paper       # paper project
      klemma init --no-input         # skip prompts, use defaults
      klemma init --force            # re-run wizard, prefill from existing config
      klemma init --global-only      # only create system config
      klemma init --type paper --name "My Paper" --language en
    """
    from .setup import InitValues, init_project, init_system

    # --non-interactive is an alias for --no-input
    if non_interactive:
        no_input = True

    # If any value flags provided, auto-imply non-interactive mode
    has_value_flags = any(v is not None for v in [project_name, description, keywords, language])
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

    # Ensure system config exists
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

    values = None
    if not no_input:
        values = _interactive_init(project_type, prefill=prefill)
        project_type = values.project_type
    elif has_value_flags:
        # Build InitValues from CLI flags
        kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else []
        values = InitValues(
            project_type=project_type,
            title=project_name or "",
            description=description or "",
            keywords=kw_list,
            language=language or "ru",
        )

    result = init_project(project_dir, project_type=project_type, values=values)

    if result["created"]:
        console.print(f"\n[green]Initialized klemma {project_type} project in {project_dir}/[/green]")
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
            from .config import update_project_config
            update_project_config(project_dir, {})  # ensure file exists
            # Write inherit_db: false to state section
            cfg_path = project_dir / ".klemma" / "config.yaml"
            raw = _load_yaml(cfg_path)
            raw.setdefault("state", {})["inherit_db"] = False
            import yaml
            cfg_path.write_text(
                yaml.dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            console.print("[dim]  inherit_db: false (parent library not inherited)[/dim]")
        else:
            console.print("[dim]  inherit_db: true (parent library will be inherited)[/dim]")

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
            from .config import scan_project_files
            from .skills.outliner import generate_outline as gen_outline
            from .skills.outliner import save_outline

            project_files = scan_project_files(kctx.project_root)
            if not project_files:
                console.print("[yellow]No files found in project directory; skipping outline.[/yellow]")
            else:
                try:
                    ai = _init_ai(kctx.config)
                except Exception as e:
                    console.print("[yellow]Skipping outline: AI backend not configured.[/yellow]")
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
                        saved_path = save_outline(result, kctx.project_root.name, kctx.project_root)
                        console.print(f"[dim]Outline saved: {saved_path}[/dim]")

    console.print()
    console.print("Run [bold]klemma status[/bold] to verify.")


def _load_prefill(config_path: Path) -> dict:
    """Read existing .klemma/config.yaml and return values for prefilling the wizard."""
    import yaml as _yaml

    try:
        raw = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

    project = raw.get("project", {}) if isinstance(raw.get("project"), dict) else {}
    ai = raw.get("ai", {}) if isinstance(raw.get("ai"), dict) else {}
    obsidian = raw.get("obsidian", {}) if isinstance(raw.get("obsidian"), dict) else {}
    zotero = raw.get("zotero", {}) if isinstance(raw.get("zotero"), dict) else {}

    return {
        "project_type": project.get("type", "dissertation"),
        "title": project.get("title", ""),
        "description": project.get("description", ""),
        "keywords": project.get("priority_terms", []),
        "language": ai.get("language", "ru"),
        "vault_path": obsidian.get("vault_path", ""),
        "notes_folder": obsidian.get("notes_folder", "References"),
        "tags_folder": obsidian.get("tags_folder", "Tags"),
        "zotero_storage": zotero.get("storage_path", ""),
        "zotero_library_json": zotero.get("library_json", ""),
    }


def _discover_paper_sources(project_dir: Path, values):
    """Scan vault + BBT JSON for sources matching paper keywords, register matches."""
    from .discovery import discover_relevant_sources
    from .library_provider import LocalLibrary

    library = LocalLibrary(Path(values.zotero_library_json))
    if not library.entries:
        return

    matches = discover_relevant_sources(
        vault_path=Path(values.vault_path),
        notes_folder=values.notes_folder,
        library_entries=library.entries,
        keywords=values.keywords,
        description=values.description,
    )

    if not matches:
        click.echo("\n  No matching sources found in vault.")
        click.echo("  Add sources later with: klemma process <citekey>")
        return

    total_in_vault = 0
    notes_dir = Path(values.vault_path) / values.notes_folder
    if notes_dir.is_dir():
        total_in_vault = sum(1 for f in notes_dir.iterdir() if f.name.startswith("@"))

    click.echo("\n  Scanning vault for relevant sources...")
    click.echo(f"  Found {len(matches)} matching sources (of {total_in_vault} in vault):")

    shown = matches[:10]
    for m in shown:
        title_short = m["title"][:60] + "..." if len(m["title"]) > 60 else m["title"]
        click.echo(f"    @{m['citekey']} — {title_short}")
    if len(matches) > 10:
        click.echo(f"    ... and {len(matches) - 10} more")

    if not click.confirm("  Include these sources?", default=True):
        click.echo("  Skipped. Add sources later with: klemma process <citekey>")
        return

    # Register sources in the project's DB
    from .state import StateManager

    db_path = project_dir / ".klemma" / "data" / "klemma.db"
    state = StateManager(str(db_path))

    citekeys = [m["citekey"] for m in matches]
    state.register_sources(citekeys)
    click.echo(f"  {len(citekeys)} sources registered.")


def _interactive_init(project_type: str, prefill: dict | None = None):
    """Run interactive setup wizard, return collected values.

    If prefill is provided (from --force), uses those values as defaults.
    """
    from .discovery import (
        detect_language,
        discover_bbt_json,
        discover_obsidian_vault,
        discover_zotero_storage,
    )
    from .setup import InitValues

    pf = prefill or {}

    click.echo("\nKlemma project setup\n")

    # --- Project basics ---
    project_type = click.prompt(
        "  Project type",
        type=click.Choice(["dissertation", "paper", "thesis"], case_sensitive=False),
        default=pf.get("project_type", project_type),
    )
    title = click.prompt(
        "  Project title",
        default=pf.get("title", ""),
        show_default=bool(pf.get("title")),
    )

    # Paper-specific: description and keywords are essential for source discovery
    description = ""
    keywords: list[str] = []
    if project_type == "paper":
        description = click.prompt(
            "  Research description (1-2 sentences)",
            default=pf.get("description", ""),
            show_default=bool(pf.get("description")),
        )
        kw_default = ", ".join(pf["keywords"]) if pf.get("keywords") else ""
        kw_str = click.prompt(
            "  Keywords (comma-separated)",
            default=kw_default,
            show_default=bool(kw_default),
        )
        keywords = [k.strip() for k in kw_str.split(",") if k.strip()] if kw_str else []

    language = click.prompt(
        "  AI language",
        default=pf.get("language", detect_language()),
    )

    # --- Auto-discovery (prefill overrides discovery) ---
    click.echo("\n  Detecting paths...")

    values = InitValues(
        project_type=project_type,
        title=title,
        description=description,
        keywords=keywords,
        language=language,
    )

    # Obsidian vault
    prefill_vault = pf.get("vault_path", "")
    vault = discover_obsidian_vault()
    discovered_vault = str(vault) if vault else ""
    # Use prefill if available, otherwise use discovery
    effective_vault = prefill_vault or discovered_vault

    if effective_vault:
        click.echo(f"  + Obsidian vault: {effective_vault}")
        if not click.confirm("    Use this path?", default=True):
            vault_str = click.prompt("    Obsidian vault path", default="")
            values.vault_path = vault_str
        else:
            values.vault_path = effective_vault
    else:
        vault_str = click.prompt(
            "  ? Obsidian vault not found. Path (empty to skip)",
            default="",
            show_default=False,
        )
        values.vault_path = vault_str

    # Zotero storage
    prefill_storage = pf.get("zotero_storage", "")
    storage = discover_zotero_storage()
    effective_storage = prefill_storage or (str(storage) if storage else "")

    if effective_storage:
        click.echo(f"  + Zotero storage: {effective_storage}")
        values.zotero_storage = effective_storage
    else:
        storage_str = click.prompt(
            "  ? Zotero storage not found. Path (empty to skip)",
            default="",
            show_default=False,
        )
        values.zotero_storage = storage_str

    # BBT JSON
    prefill_bbt = pf.get("zotero_library_json", "")
    bbt = discover_bbt_json()
    effective_bbt = prefill_bbt or (str(bbt) if bbt else "")

    if effective_bbt:
        click.echo(f"  + BBT JSON export: {effective_bbt}")
        values.zotero_library_json = effective_bbt
    else:
        bbt_str = click.prompt(
            "  ? BBT JSON export not found. Path (empty to skip)",
            default="",
            show_default=False,
        )
        values.zotero_library_json = bbt_str

    return values


@main.command()
@click.pass_context
def plan(ctx):
    """Daily plan — focus, recommendations, deadlines."""
    kctx = _get_context(ctx)
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
@click.option("--force", is_flag=True,
              help="Reprocess completed sources, replacing existing fragments")
@click.option("--model", default=None, help="Override AI model (e.g. openai/gpt-4.1-mini)")
@click.pass_context
def process(ctx, citekeys, serial, force, model):
    """Process source(s): extract fragments, annotate, create vault note.

    With CITEKEY(s): process specified sources (parallel when >1).
    Without arguments: process all pending sources.
    With --force: reprocess all completed sources, replacing their fragments.
    """
    kctx = _get_context(ctx)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    if model:
        cfg.ai.model = model
    ai = _init_ai(cfg)

    from .literature.pdf import PDFExtractor

    pdf_extractor = PDFExtractor(max_chars=cfg.ai.max_pdf_chars)

    # Auto-resolve previously detected reference gaps against current library
    if kctx.library:
        resolved = state.resolve_gaps(kctx.library.entries)
        if resolved:
            console.print(f"[green]Auto-resolved {resolved} reference gap(s)[/green]")

    # Build citekey list: explicit, force-completed, or all pending
    if citekeys:
        keys = list(citekeys)
    elif force:
        keys = state.get_completed_sources()
        if not keys:
            console.print("[green]No completed sources to reprocess.[/green]")
            return
        console.print(f"[blue]Reprocessing {len(keys)} completed sources (replacing fragments)[/blue]")
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
                               klemma_home=kctx.klemma_home, embeddings=kctx.embeddings, force=force): ck
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
                                         klemma_home=kctx.klemma_home,
                                         project_type=kctx.project.type if kctx.project else "dissertation",
                                         embeddings=kctx.embeddings, force=force)
            if n_frags > 0:
                processed += 1
        if len(keys) > 1:
            console.print(f"\n[green]Done: {processed}/{len(keys)} processed.[/green]")

    # DEV mode: show benchmark candidate hints
    if kctx.config.instance.dev_mode:
        from .evaluation.candidates import discover_candidates, format_candidate_hint
        candidates = discover_candidates(kctx.state, limit=3)
        hint = format_candidate_hint(candidates)
        if hint:
            console.print(hint)


def _process_single(citekey, cfg, state, vault, ai, pdf_extractor, library, quiet=False,
                    dissertation_context="", available_tags=None, klemma_home=None,
                    project_type="dissertation", embeddings=None, force=False):
    """Process a single source: find PDF, extract fragments, save to vault.

    Returns (fragment_count, status_message). When quiet=True, suppresses console output
    (used for parallel execution). When force=True, existing fragments are deleted before
    extraction so the source is fully reprocessed.
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

    # If reprocessing, clear old fragments before extracting fresh ones
    if force:
        state.delete_fragments(citekey)

    # Extract fragments
    result = extract_fragments(
        entry, pdf_text, cfg, state, ai,
        dissertation_context=dissertation_context,
        available_tags=available_tags,
        klemma_home=klemma_home,
        project_type=project_type,
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

    # Auto-embed if provider available and entry has abstract
    if embeddings and entry.abstract:
        try:
            vec = embeddings.embed(entry.title or citekey, entry.abstract)
            if vec:
                state.save_embedding(citekey, vec, embeddings.model_name)
                if not quiet:
                    console.print(f"  [dim]embedded ({embeddings.model_name})[/dim]")
        except Exception as e:
            if not quiet:
                console.print(f"  [dim]embed failed: {e}[/dim]")

    return (len(result.fragments), "ok")


@main.command()
@click.argument("citekeys", required=False, nargs=-1)
@click.option("--dry-run", is_flag=True, help="Show how many would be embedded without calling API")
@click.option("--backend", type=click.Choice(["s2", "local", "openai"]), help="Override embedding backend")
@click.option("--fragments", is_flag=True, help="Embed fragments instead of sources")
@click.pass_context
def embed(ctx, citekeys, dry_run, backend, fragments):
    """Backfill embeddings for sources with abstracts.

    Without CITEKEYS: embed all sources missing embeddings.
    With CITEKEYS: embed specific sources.
    Use --fragments to embed fragment text instead of source title+abstract.
    Use --dry-run to preview without API calls.
    """
    kctx = _get_context(ctx)
    state = kctx.state

    # Determine embedding provider
    if backend:
        from .embeddings import create_embeddings as _create_emb
        emb = _create_emb({"backend": backend})
    else:
        emb = kctx.embeddings

    if not emb and not dry_run:
        console.print(
            "[red]No embedding backend configured.[/red]\n"
            "Set embeddings.backend in config.yaml (s2, local, openai) "
            "or use --backend flag."
        )
        return

    if fragments:
        # Fragment embedding mode
        candidates = state.get_unembedded_fragments()
        if not candidates:
            console.print("[green]All fragments already have embeddings.[/green]")
            return
        if dry_run:
            console.print(f"[blue]Would embed {len(candidates)} fragments[/blue]")
            return

        embedded = 0
        failed = 0
        from rich.progress import Progress
        with Progress(console=console) as progress:
            task = progress.add_task("Embedding fragments...", total=len(candidates))
            for frag in candidates:
                try:
                    vec = emb.embed(frag["fragment_text"])
                    if vec:
                        state.save_fragment_embedding(frag["id"], vec, emb.model_name)
                        embedded += 1
                    else:
                        failed += 1
                except Exception as e:
                    console.print(f"  [red]Fragment {frag['id']}: {e}[/red]")
                    failed += 1
                progress.advance(task)

        console.print(f"\n[green]Embedded: {embedded}[/green]", end="")
        if failed:
            console.print(f" | [red]Failed: {failed}[/red]", end="")
        console.print()
        return

    # Get candidates: sources with abstract but no embedding
    if citekeys:
        candidates = []
        missing = []
        for ck in citekeys:
            source = state.get_source(ck)
            if not source:
                missing.append(ck)
                continue
            candidates.append(ck)
        if missing:
            console.print(f"[yellow]Missing citekeys: {', '.join(missing)}[/yellow]")
        if not candidates:
            return
    else:
        # Find completed sources without embeddings
        candidates = state.get_sources_without_embeddings()

    if not candidates:
        console.print("[green]All sources already have embeddings.[/green]")
        return

    # For dry-run, check which have abstracts via library
    if kctx.library:
        entries = kctx.library.entries
        with_abstract = [c for c in candidates if entries.get(c) and entries[c].abstract]
        without_abstract = len(candidates) - len(with_abstract)
    else:
        with_abstract = candidates
        without_abstract = 0

    if dry_run:
        console.print(f"[blue]Would embed {len(with_abstract)} sources[/blue]")
        if without_abstract:
            console.print(f"[dim]{without_abstract} sources have no abstract (will be skipped)[/dim]")
        return

    # Embed
    embedded = 0
    skipped = 0
    failed = 0
    entries = kctx.library.entries if kctx.library else {}

    from rich.progress import Progress
    with Progress(console=console) as progress:
        task = progress.add_task("Embedding...", total=len(candidates))
        for ck in candidates:
            entry = entries.get(ck)
            title = entry.title if entry else ck
            abstract = entry.abstract if entry else ""
            if not abstract:
                skipped += 1
                progress.advance(task)
                continue
            try:
                vec = emb.embed(title, abstract)
                if vec:
                    state.save_embedding(ck, vec, emb.model_name)
                    embedded += 1
                else:
                    skipped += 1
            except Exception as e:
                console.print(f"  [red]{ck}: {e}[/red]")
                failed += 1
            progress.advance(task)

    console.print(f"\n[green]Embedded: {embedded}[/green]", end="")
    if skipped:
        console.print(f" | [yellow]Skipped: {skipped}[/yellow]", end="")
    if failed:
        console.print(f" | [red]Failed: {failed}[/red]", end="")
    console.print()


@main.command()
@click.argument("citekey_or_section")
@click.option("-k", "--top-k", default=10, help="Number of results (default: 10)")
@click.pass_context
def similar(ctx, citekey_or_section, top_k):
    """Find semantically similar sources.

    CITEKEY: find sources similar to a specific paper.
    SECTION (e.g. 2.3): find sources close to that section's centroid
    (useful for discovering cross-section recommendations).

    Requires embeddings to be stored (run `klemma embed` first).
    """
    from .embeddings import cosine_similarity

    kctx = _get_context(ctx)
    state = kctx.state
    emb = kctx.embeddings

    if not emb:
        # Still try to use stored embeddings for comparison
        all_emb = state.get_all_embeddings()
        if not all_emb:
            console.print(
                "[red]No embeddings found. Run `klemma embed` first.[/red]"
            )
            return
        model_name = None
    else:
        model_name = emb.model_name
        all_emb = state.get_all_embeddings(model=model_name)
        if not all_emb:
            console.print(
                "[red]No embeddings found. Run `klemma embed` first.[/red]"
            )
            return

    # Determine if input is a citekey or section
    arg = citekey_or_section
    is_section = bool(arg[0].isdigit() and "." in arg)

    if is_section:
        # Section centroid mode
        section_sources = state.get_section_sources(arg)
        section_vecs = [all_emb[sid] for sid in section_sources if sid in all_emb]
        if not section_vecs:
            console.print(f"[red]No embedded sources for section {arg}[/red]")
            return
        dim = len(section_vecs[0])
        query_vec = [
            sum(v[i] for v in section_vecs) / len(section_vecs)
            for i in range(dim)
        ]
        console.print(
            f"[bold]Sources similar to section {arg}[/bold] "
            f"[dim](centroid of {len(section_vecs)} sources)[/dim]\n"
        )
        # Exclude sources already in this section
        exclude = set(section_sources)
    else:
        # Citekey mode
        result = state.get_embedding(arg)
        if not result:
            # Try to embed on the fly
            if emb and kctx.library:
                entry = kctx.library.entries.get(arg)
                if entry and entry.abstract:
                    vec = emb.embed(entry.title or arg, entry.abstract)
                    if vec:
                        state.save_embedding(arg, vec, emb.model_name)
                        query_vec = vec
                        console.print(f"[dim]Embedded @{arg} on the fly[/dim]\n")
                    else:
                        console.print(f"[red]Could not embed @{arg}[/red]")
                        return
                else:
                    console.print(f"[red]No embedding for @{arg} and no abstract to embed[/red]")
                    return
            else:
                console.print(f"[red]No embedding for @{arg}. Run `klemma embed {arg}` first.[/red]")
                return
        else:
            query_vec = result[0]
        console.print(f"[bold]Sources similar to @{arg}[/bold]\n")
        exclude = {arg}

    # Compute similarities
    sims = []
    for sid, vec in all_emb.items():
        if sid in exclude:
            continue
        sim = cosine_similarity(query_vec, vec)
        sims.append((sid, sim))
    sims.sort(key=lambda x: x[1], reverse=True)

    # Display
    entries = kctx.library.entries if kctx.library else {}
    table = Table(show_edge=False, pad_edge=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("Source", style="cyan")
    table.add_column("Similarity", justify="right", width=8)
    table.add_column("Section", width=8)

    for i, (sid, sim) in enumerate(sims[:top_k], 1):
        entry = entries.get(sid)
        title = f"@{sid}"
        if entry:
            author_short = (entry.authors_str or "")[:25]
            year = entry.year or ""
            title = f"{author_short} ({year})"
        source = state.get_source(sid)
        sec = source.get("primary_section", "") if source else ""
        style = "green" if sim > 0.8 else "yellow" if sim > 0.5 else "dim"
        table.add_row(str(i), title, f"[{style}]{sim:.3f}[/{style}]", sec)

    console.print(table)

    if is_section:
        # Show cross-section discoveries
        cross = [(sid, sim) for sid, sim in sims[:top_k]
                 if not any(sid in state.get_section_sources(arg))]
        if cross:
            console.print(f"\n[dim]{len(cross)} sources from other sections[/dim]")


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="Show full detailed tables")
@click.option("--chapter", "-ch", type=int, help="Filter by chapter")
@click.pass_context
def status(ctx, verbose, chapter):
    """Unified status: processing, coverage, gaps, reference gaps."""
    kctx = _get_context(ctx)
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

    # --- Coverage (chapter-based for dissertation/thesis, simple for paper) ---
    has_chapters = project and project.chapters and project.type != "paper"

    if has_chapters:
        chapter_numbers = project.chapter_numbers
        table = Table(title="Coverage by Chapter", show_edge=False, pad_edge=False)
        table.add_column("Chapter", style="cyan")
        table.add_column("Sources", justify="right", width=8)
        for ch in chapter_numbers:
            if chapter and ch != chapter:
                continue
            count = cov["chapters"].get(ch, 0)
            style = "green" if count >= 10 else "yellow" if count >= 5 else "red"
            name = project.chapters.get(ch, "")
            table.add_row(f"Ch {ch}: {name}", f"[{style}]{count}[/{style}]")
        console.print(table)

        # Sections (verbose or filtered by chapter)
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
    elif not project or project.type == "paper":
        # Paper: show section coverage if any, no chapter structure
        if cov["sections"]:
            sec_table = Table(title="Coverage by Section", show_edge=False, pad_edge=False)
            sec_table.add_column("Section", style="cyan")
            sec_table.add_column("Sources", justify="right", width=8)
            for sec, count in sorted(cov["sections"].items()):
                style = "green" if count >= 3 else "yellow" if count >= 1 else "red"
                sec_table.add_row(sec, f"[{style}]{count}[/{style}]")
            console.print(sec_table)
    else:
        # Fallback: legacy dissertation config
        chapter_numbers = list(range(1, 5))
        table = Table(title="Coverage by Chapter", show_edge=False, pad_edge=False)
        table.add_column("Chapter", style="cyan")
        table.add_column("Sources", justify="right", width=8)
        for ch in chapter_numbers:
            count = cov["chapters"].get(ch, 0)
            style = "green" if count >= 10 else "yellow" if count >= 5 else "red"
            name = cfg.dissertation.chapters.get(ch, "")
            table.add_row(f"Ch {ch}: {name}", f"[{style}]{count}[/{style}]")
        console.print(table)

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
    _sw = kctx.project.section_weights if kctx.project else None
    if verbose:
        _print_ref_gaps_table(state, limit=20, embeddings=kctx.embeddings,
                              section_weights=_sw)
    else:
        ref_gaps = state.get_reference_gaps(limit=5, section_weights=_sw)
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

    # --- Verbose: intent coverage matrix ---
    if verbose:
        intent_cov = state.get_intent_coverage()
        if intent_cov:
            console.print()
            it = Table(title="Intent Coverage", show_edge=False, pad_edge=False)
            it.add_column("Section", style="cyan")
            it.add_column("Background", justify="right")
            it.add_column("Method", justify="right")
            it.add_column("Result", justify="right")
            it.add_column("Total", justify="right", style="bold")
            for sec in sorted(intent_cov):
                if chapter and not sec.startswith(f"{chapter}."):
                    continue
                d = intent_cov[sec]
                it.add_row(
                    sec,
                    str(d["background"]) if d["background"] else "[dim]0[/dim]",
                    str(d["method"]) if d["method"] else "[dim]0[/dim]",
                    str(d["result_comparison"]) if d["result_comparison"] else "[dim]0[/dim]",
                    str(d["total"]),
                )
            console.print(it)

    # --- Verbose: embedding stats ---
    if verbose:
        emb_stats = state.get_embedding_stats()
        if emb_stats["embedded"] > 0 or emb_stats["total"] > 0:
            console.print()
            pct = (emb_stats["embedded"] / emb_stats["total"] * 100) if emb_stats["total"] else 0
            console.print(
                f"[bold]Embeddings[/bold]: {emb_stats['embedded']}/{emb_stats['total']} "
                f"sources ({pct:.0f}%)"
            )
            for model, cnt in emb_stats["models"].items():
                console.print(f"  [dim]{model}: {cnt}[/dim]")

    # --- Verbose: citation graph stats ---
    if verbose:
        graph = state.get_citation_graph_stats()
        if graph["total_links"] > 0:
            console.print()
            console.print(
                f"[bold]Citation Graph[/bold]: {graph['total_links']} links, "
                f"{graph['unique_targets']} unique targets "
                f"({graph['in_library']} in library, {graph['external']} external)"
            )
            console.print(
                f"  [dim]{graph['source_count']} citing sources, "
                f"avg {graph['avg_refs_per_source']} refs/source[/dim]"
            )
            if graph["most_cited_external"]:
                console.print()
                console.print("[bold]Most Cited External[/bold] [dim](bridging nodes)[/dim]")
                for ref in graph["most_cited_external"][:5]:
                    authors = (ref["target_authors"] or "")[:25]
                    year = ref["target_year"] or ""
                    console.print(
                        f"  [yellow]x{ref['cite_count']}[/yellow]  "
                        f"{authors} ({year}) [dim]{(ref['target_title'] or '')[:40]}[/dim]"
                    )
            if graph["most_connected_internal"]:
                console.print()
                console.print("[bold]Most Connected Internal[/bold]")
                for ref in graph["most_connected_internal"][:5]:
                    console.print(
                        f"  [green]x{ref['cite_count']}[/green]  @{ref['target_citekey']}"
                    )

    # --- Recommended actions ---
    _emb = state.get_embedding_stats()
    _prune = state.get_prune_summary()
    _ref = state.get_reference_gaps(limit=3, section_weights=_sw)
    _print_recommended_actions(proc_stats, _emb, gaps_data, _ref, _prune)


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
@click.option("--model", default=None, help="Override AI model (e.g. openai/gpt-4.1-mini)")
@click.pass_context
def research(ctx, section, no_save, force, model):
    """Deep section analysis — argument structure, citation plan, gaps.

    Auto-processes unextracted sources before analysis.
    Use --force to re-extract all fragments.

    Example: klemma research --section 1.3.2
    """
    kctx = _get_context(ctx)
    cfg, state, vault = kctx.config, kctx.state, kctx.vault
    _sync_sections(kctx)
    if model:
        cfg.ai.model = model
    ai = _init_ai(cfg)

    from .config import parse_chapter_from_section
    from .skills.researcher import pre_extract_sources, research_section

    chapter = parse_chapter_from_section(section)

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
            section, cfg, state, vault, ai, save_to_vault=not no_save,
            project=kctx.project,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
            project_root=kctx.project_root,
            embeddings=kctx.embeddings,
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

    # Отфильтрованные цитаты
    if result.filtered_citekeys:
        console.print(
            f"\n[yellow]Removed {len(result.filtered_citekeys)} hallucinated citekeys "
            f"(not in library): {result.filtered_citekeys}[/yellow]"
        )

    # Сохранение
    if not no_save:
        console.print(f"\n[dim]Брифинг сохранён: notes/research/Research_{section}.md[/dim]")


@main.command()
@click.option("--no-save", is_flag=True, help="Show outline without saving")
@click.option("--scan-only", is_flag=True, help="Show found files without AI generation")
@click.option("-p", "--prompt", default="", help="Custom directive for AI (e.g. 'Focus on knowledge graph')")
@click.option("--fresh", is_flag=True, help="Force full regeneration, ignore previous outline")
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

    from .config import scan_project_files
    from .skills.outliner import generate_outline as gen_outline
    from .skills.outliner import save_outline

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
        size_str = f"{pf['size']:,} B" if pf['size'] < 10000 else f"{pf['size'] // 1024} KB"
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
            cfg, state, ai, kctx.project_root,
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
    console.print(Panel(
        f"[bold]{result.title}[/bold]\n\n{result.description}",
        title="Outline",
        border_style="blue",
    ))

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
                f"{k} {v}" for k, v in sorted(result.sections.items())
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

    # 5. Save to project_root (no config.yaml, KLEMMA.md, or vault writes)
    saved_path = save_outline(result, project_name, kctx.project_root)
    console.print(f"\n[dim]Saved: {saved_path}[/dim]")


@main.command(name="import", hidden=True)
@click.option("--with-queue", is_flag=True, help="Also populate reading queue from high-priority sources")
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
@click.option("--model", default=None, help="Override AI model (e.g. openai/gpt-4.1-mini)")
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

    from .skills.agent import build_agent_context, update_agents_index

    with console.status("Сборка контекста исследования", spinner="dots"):
        context = build_agent_context(
            cfg, state, vault, section=section, chapter=chapter,
            project=kctx.project,
            dissertation_context=kctx.dissertation_context,
            klemma_home=kctx.klemma_home,
            project_name=kctx.project_name,
            project_root=kctx.project_root,
            embeddings=kctx.embeddings,
            query=query,
        )

    # Show RAG status
    if kctx.embeddings:
        frag_stats = state.get_fragment_embedding_stats()
        if frag_stats["embedded"] > 0:
            console.print(f"[dim]RAG: {frag_stats['embedded']} fragment embeddings available[/dim]")
        else:
            console.print("[dim]RAG: no fragment embeddings (run klemma embed --fragments)[/dim]")

    console.print(f"[dim]Query: {query}[/dim]")

    response = None
    if ai.interactive_available:
        import subprocess as _sp

        result = _sp.run(
            ["claude", "-p", "--model", cfg.ai.model, "--system-prompt", context, query],
            capture_output=True, text=True, timeout=cfg.ai.timeout,
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
            from .ai import resolve_task_model

            response = ai.call(
                system=context, user=query, max_tokens=8192,
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
            f"---\ntype: agent\ndate: {today}\n"
            f"query: \"{query[:200]}\"\n---\n\n"
        )
        save_path.write_text(frontmatter + response, encoding="utf-8")
        console.print(f"\n[green]Saved: {save_path}[/green]")

        idx = update_agents_index(kctx.project_root)
        if idx:
            console.print("[dim]Updated notes/AGENTS.md[/dim]")

    console.print("[dim]Сессия агента завершена.[/dim]")


@main.group(invoke_without_command=True)
@click.option("--section", "-s", help="Focus on a specific section (recommend mode)")
@click.option("--audit", is_flag=True, help="Deep quality audit")
@click.option("--model", default=None, help="Override AI model (e.g. openai/gpt-4.1-mini)")
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

    from .skills.librarian import analyze_library

    entry_lookup = kctx.library.entries if kctx.library else {}

    mode = "audit" if audit else "recommend" if section else "status"

    with console.status(f"Analyzing library ({mode})", spinner="dots"):
        report = analyze_library(
            cfg, state, vault, ai, entry_lookup, mode=mode, focus_section=section,
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

    # Author network (audit mode)
    if audit:
        author_groups = state.get_key_author_groups(min_papers=2)
        if author_groups:
            console.print("\n[bold]Key Author Groups[/bold] [dim](2+ papers in citation graph)[/dim]")
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
    _print_ref_gaps_table(state, embeddings=kctx.embeddings)

    if kctx.project_root:
        console.print("\n[dim]Full report saved to notes/library/[/dim]")
    else:
        console.print("\n[dim]Full report saved to vault.[/dim]")


@library.command()
@click.option("-c", "--chapter", type=int, help="Filter by chapter number")
@click.option("-v", "--verdict", type=click.Choice(["drop", "maybe"]), help="Filter by verdict")
@click.option("--clear", "clear_key", help="Clear verdict for a citekey")
@click.pass_context
def prune(ctx, chapter, verdict, clear_key):
    """Browse and manage prune verdicts from library analysis."""
    kctx = _get_context(ctx)
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
    from .skills.acquirer import PaperMetadata, acquire_paper_local, load_batch

    kctx = _get_context(ctx)
    cfg, state = kctx.config, kctx.state

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

    ok = 0

    for i, meta in enumerate(papers, 1):
        label = meta.title[:50] if meta.title else meta.url[:50]
        console.print(f"\n[bold][{i}/{len(papers)}] {label}[/bold]")

        result = acquire_paper_local(
            meta, storage_path=cfg.zotero.storage_path, state=state,
        )

        if result.status == "ok":
            console.print(f"  [green]@{result.citekey}[/green]")
            if meta.title:
                auto = " [dim](auto-extracted)[/dim]" if not title else ""
                console.print(f"  Title: {meta.title}{auto}")
            if meta.authors:
                console.print(f"  Authors: {meta.authors}")
            if meta.year:
                console.print(f"  Year: {meta.year}")
            if result.zotero_added:
                console.print("  [blue]Added to Zotero (BBT citekey)[/blue]")
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
                                        klemma_home=kctx.klemma_home,
                                        project_type=kctx.project.type if kctx.project else "dissertation")

            ok += 1
        else:
            console.print(f"  [red]{result.status}[/red]")

    console.print(f"\n[green]Done: {ok}/{len(papers)} acquired.[/green]")

    # DEV mode: show benchmark candidate hints
    if kctx.config.instance.dev_mode:
        from .evaluation.candidates import discover_candidates, format_candidate_hint
        candidates = discover_candidates(kctx.state, limit=3)
        hint = format_candidate_hint(candidates)
        if hint:
            console.print(hint)



# --- Info & Tree: project introspection ---

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

    console.print(Panel(
        "\n".join(info_parts),
        title=f"Project: {kctx.project_name}",
        border_style="blue",
    ))

    # Effective Zotero config (merged from system + parent + project)
    zot = kctx.config.zotero
    zot_parts = []
    if zot.library_json:
        zot_parts.append(f"BBT JSON: {zot.library_json}")
    if zot.storage_path:
        zot_parts.append(f"Storage: {zot.storage_path}")
    if zot_parts:
        console.print(Panel(
            "\n".join(zot_parts),
            title="Zotero (effective)",
            border_style="dim",
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


# --- Benchmark: evaluation framework ---


def _run_auto_mode(kctx, paper_citekey, skip_prepare, ablation=None):
    """Run full autonomous benchmark pipeline."""
    from .evaluation.pipeline import run_auto_benchmark

    try:
        ai = _init_ai(kctx.config)
    except Exception as e:
        console.print(f"[red]Failed to initialize AI: {e}[/red]")
        return

    target = paper_citekey or "[auto-select]"
    console.print(f"[bold]Auto benchmark: {target}[/bold]")

    if ablation:
        params = ablation.to_snapshot()
        non_default = {k: v for k, v in params.items()
                       if v is not None and k != "prompt_variant"}
        if non_default or params.get("prompt_variant") != "default":
            console.print(f"[dim]Ablation: {params}[/dim]")

    with console.status("Running autonomous benchmark pipeline...", spinner="arc"):
        result = run_auto_benchmark(
            kctx.state, ai, kctx.config,
            klemma_home=kctx.klemma_home,
            paper_citekey=paper_citekey,
            skip_prepare=skip_prepare,
            storage_path=kctx.config.zotero.storage_path,
            ablation=ablation,
        )

    if result.results.get("error"):
        console.print(f"[red]Pipeline failed: {result.results['error']}[/red]")
        return

    console.print(f"[green]Paper: {result.paper_citekey}[/green]")

    if result.prepare_result:
        pr = result.prepare_result
        console.print(
            f"[dim]Prepared: {pr.fetched} fetched, "
            f"{pr.in_library} in library, "
            f"{pr.unfetchable} unavailable[/dim]"
        )

    if "reconstruction" in result.results:
        _print_reconstruction_results(result.results["reconstruction"])

    console.print(f"\n[dim]Run {result.run_id} saved[/dim]")

    if result.comparison:
        console.print(f"[dim]Compared with previous: {result.previous_run_id}[/dim]")
        _print_benchmark_compare(kctx.state, result.previous_run_id, result.run_id)


def _run_prepare_mode(kctx, citekey: str):
    """Resolve and fetch missing referenced papers for benchmarking."""
    from .evaluation.prepare import prepare_benchmark

    source = kctx.state.get_source(citekey)
    if not source:
        console.print(f"[red]Source {citekey} not found in DB[/red]")
        return

    # Always dry-run first
    console.print(f"Scanning references for {citekey}...")
    result = prepare_benchmark(
        kctx.state, citekey,
        storage_path=kctx.config.zotero.storage_path,
        dry_run=True,
    )

    if not result.references:
        console.print("[yellow]No citation links found for this paper[/yellow]")
        return

    # Display reference table
    t = Table(title=f"References for {citekey}")
    t.add_column("Title", max_width=50)
    t.add_column("Status")
    t.add_column("Source")
    t.add_column("PDF")
    for ref in result.references:
        source_str = ref.resolved.source if ref.resolved else ""
        pdf_str = "[green]yes[/green]" if ref.resolved and ref.resolved.pdf_url else "[red]no[/red]"
        status_color = {
            "in_library": "green", "resolved": "blue",
            "no_pdf": "yellow", "failed": "red",
        }.get(ref.status, "dim")
        t.add_row(
            ref.title[:50],
            f"[{status_color}]{ref.status}[/{status_color}]",
            source_str,
            pdf_str if ref.status != "in_library" else "[dim]-[/dim]",
        )
    console.print(t)
    console.print(
        f"Total: {result.total_references}, In library: {result.in_library}, "
        f"Resolvable: {len([r for r in result.references if r.status == 'resolved'])}, "
        f"No PDF: {result.unfetchable}"
    )

    fetchable = [r for r in result.references if r.status == "resolved"]
    if not fetchable:
        console.print("[dim]No new papers to fetch.[/dim]")
        return

    if not click.confirm(f"Download {len(fetchable)} papers?"):
        return

    # Actual fetch
    result = prepare_benchmark(
        kctx.state, citekey,
        storage_path=kctx.config.zotero.storage_path,
        dry_run=False,
    )
    console.print(
        f"[green]Fetched {result.fetched} papers[/green]"
        f"[yellow] ({result.unfetchable} unavailable)[/yellow]"
    )


def _run_analyst_mode(kctx, citekey: str, json_output: bool):
    """Run analyst prompt to extract ground truth from a paper's PDF."""
    import json

    from .evaluation.pipeline import run_analyst_from_source

    # Initialize AI
    try:
        ai = _init_ai(kctx.config)
    except Exception as e:
        console.print(f"[red]Failed to initialize AI: {e}[/red]")
        return

    console.print(f"Analyzing {citekey}...")
    dataset = run_analyst_from_source(
        kctx.state, ai, citekey, kctx.config, kctx.klemma_home,
    )

    if not dataset:
        console.print("[red]Analyst prompt failed (check logs)[/red]")
        return

    gt = dataset.ground_truth
    if json_output:
        click.echo(json.dumps(dataset.model_dump(), indent=2))
    else:
        console.print(
            f"[green]Ground truth extracted:[/green] "
            f"{len(gt.sections)} sections, "
            f"{gt.bibliography_size} references, "
            f"{len(dataset.samples)} in-library samples"
        )
        console.print("Save as JSON and add to your benchmark dataset under 'reconstruction' key.")
        click.echo(json.dumps(dataset.model_dump(), indent=2))


def _print_reconstruction_results(recon: dict):
    """Print reconstruction benchmark results as Rich panels."""
    # Ground truth summary
    gt = recon.get("ground_truth", {})
    console.print(Panel(
        f"Paper: {gt.get('paper', 'N/A')}\n"
        f"Sections: {gt.get('sections', 0)}, "
        f"Bibliography: {gt.get('bibliography_size', 0)}, "
        f"In-library samples: {gt.get('samples', 0)}",
        title="Reconstruction: Ground Truth",
    ))

    # Baseline results (source-coverage)
    bl = recon.get("baseline", {})
    if bl:
        console.print(Panel(
            f"Source coverage: {bl.get('sources_covered', 0)}/{bl.get('sources_total', 0)} "
            f"({bl.get('source_coverage', 0):.1%})\n"
            f"Intent coverage: {bl.get('intent_coverage', 0):.1%}",
            title="Reconstruction: Baseline (library coverage)",
        ))

    # AI reconstruction results
    rc = recon.get("reconstruction", {})
    if rc:
        if rc.get("error"):
            console.print(f"[yellow]Reconstruction: {rc['error']}[/yellow]")
        else:
            console.print(Panel(
                f"Predictions: {rc.get('predictions_count', 0)}\n"
                f"Macro-P: {rc.get('macro_precision', 0):.4f}  "
                f"Macro-R: {rc.get('macro_recall', 0):.4f}  "
                f"[bold]F1: {rc.get('f1', 0):.4f}[/bold]\n"
                f"Intent accuracy: {rc.get('intent_accuracy', 0):.4f}  "
                f"nDCG avg: {rc.get('ndcg_avg', 0):.4f}",
                title="Reconstruction: AI-driven",
            ))

    # Per-section table (from reconstruction only — baseline is section-agnostic)
    source = rc if rc and not rc.get("error") else None
    per_section = source.get("per_section", {}) if source else {}
    if per_section:
        t = Table(title="Per-section metrics")
        t.add_column("Section")
        t.add_column("GT", justify="right")
        t.add_column("Pred", justify="right")
        t.add_column("Hits", justify="right")
        t.add_column("Precision", justify="right")
        t.add_column("Recall", justify="right")
        t.add_column("nDCG", justify="right")
        for sec_id, vals in sorted(per_section.items()):
            t.add_row(
                sec_id,
                str(vals.get("gt_count", 0)),
                str(vals.get("pred_count", 0)),
                str(vals.get("hits", 0)),
                f"{vals.get('precision', 0):.4f}",
                f"{vals.get('recall', 0):.4f}",
                f"{vals.get('ndcg', 0):.4f}",
            )
        console.print(t)

def _print_benchmark_history(state):
    """Print benchmark run history as Rich table."""
    runs = state.get_benchmark_runs(limit=20)
    if not runs:
        console.print("[yellow]No benchmark runs found.[/yellow]")
        return
    t = Table(title="Benchmark History")
    t.add_column("Run ID")
    t.add_column("Timestamp")
    t.add_column("Paper")
    t.add_column("Metrics")
    t.add_column("F1", justify="right")
    t.add_column("Duration", justify="right")
    t.add_column("Commit")
    for r in runs:
        summary = r.get("results_summary", {})
        f1 = summary.get("reconstruction.f1", summary.get("intent.macro_f1", ""))
        f1_str = f"{f1:.4f}" if isinstance(f1, float) else str(f1)
        dur = r.get("duration_seconds", 0)
        dur_str = f"{dur:.1f}s" if dur else ""
        t.add_row(
            r.get("run_id", "")[:8],
            (r.get("timestamp", "") or "")[:19],
            r.get("paper_citekey", "") or "-",
            r.get("metrics_filter", ""),
            f1_str,
            dur_str,
            r.get("git_commit", "") or "",
        )
    console.print(t)


def _print_benchmark_compare(state, id_a: str, id_b: str):
    """Print side-by-side comparison of two benchmark runs."""
    result = state.compare_benchmark_runs(id_a, id_b)
    if result.get("error"):
        console.print(f"[red]{result['error']}[/red]")
        return
    t = Table(title=f"Compare {id_a[:8]} vs {id_b[:8]}")
    t.add_column("Metric")
    t.add_column(f"{id_a[:8]}", justify="right")
    t.add_column(f"{id_b[:8]}", justify="right")
    t.add_column("Delta", justify="right")
    for key, vals in result.get("deltas", {}).items():
        va = vals.get("a")
        vb = vals.get("b")
        delta = vals.get("delta")
        va_str = f"{va:.4f}" if isinstance(va, float) else str(va or "")
        vb_str = f"{vb:.4f}" if isinstance(vb, float) else str(vb or "")
        if delta is not None and isinstance(delta, float):
            arrow = "[green]+[/green]" if delta > 0 else "[red]" if delta < 0 else ""
            arrow_end = "[/red]" if delta < 0 else ""
            delta_str = f"{arrow}{delta:+.4f}{arrow_end}"
        else:
            delta_str = ""
        t.add_row(key, va_str, vb_str, delta_str)
    console.print(t)


@main.command()
@click.option("--dataset", "-d", type=click.Path(exists=True),
              help="Path to annotated benchmark dataset JSON")
@click.option("--metrics", "-m",
              type=click.Choice(["all", "intent", "gaps", "embeddings", "reconstruct"]),
              default="all", help="Which benchmarks to run (default: all)")
@click.option("--export", "export_path", type=click.Path(),
              help="Export current DB data as dataset template for annotation")
@click.option("--json-output", is_flag=True,
              help="Output results as JSON for reproducibility")
@click.option("--semantic", is_flag=True,
              help="Apply semantic reranking to gap benchmark (hybrid keyword × semantic mode)")
@click.option("--analyst", "analyst_citekey", type=str, default=None,
              help="Run analyst prompt on a paper PDF to extract ground truth citation map")
@click.option("--reconstruct", "reconstruct", is_flag=True,
              help="Run citation reconstruction benchmark (requires reconstruction field in dataset)")
@click.option("--history", is_flag=True,
              help="Show past benchmark run history")
@click.option("--compare", nargs=2, type=str, default=None,
              help="Compare two runs: --compare <id1> <id2>")
@click.option("--export-history", "export_history_path", type=click.Path(),
              help="Export benchmark run history as JSON for archival")
@click.option("--candidates", is_flag=True,
              help="Show benchmark candidate papers ranked by citation graph coverage")
@click.option("-k", "candidates_limit", type=int, default=10,
              help="Number of candidates to show (default: 10)")
@click.option("--prepare", "prepare_citekey", type=str, default=None,
              help="Fetch missing referenced papers for a citekey (dry-run first)")
@click.option("--auto", "auto_mode", is_flag=True,
              help="Run full autonomous pipeline: select → prepare → analyst → benchmark → persist")
@click.option("--paper", "auto_paper", type=str, default=None,
              help="Citekey for --auto mode (default: top candidate)")
@click.option("--skip-prepare", is_flag=True,
              help="Skip reference preparation in --auto mode")
@click.option("--temperature", "ablation_temperature", type=float, default=None,
              help="Override AI temperature for ablation (default: 0.2)")
@click.option("--max-recs", "ablation_max_recs", type=int, default=None,
              help="Max recommendations per section (default: uncapped)")
@click.option("--fragments", "ablation_fragments", type=int, default=None,
              help="Fragments per source for context (default: 5)")
@click.option("--prompt-variant", "ablation_variant", type=click.Choice(["default", "fewshot"]),
              default=None, help="Prompt variant for ablation (default: default)")
@click.pass_context
def benchmark(ctx, dataset, metrics, export_path, json_output, semantic,
              analyst_citekey, reconstruct, history, compare, export_history_path,
              candidates, candidates_limit, prepare_citekey,
              auto_mode, auto_paper, skip_prepare,
              ablation_temperature, ablation_max_recs, ablation_fragments,
              ablation_variant):
    """Run evaluation benchmarks against annotated ground truth.

    Multi-format evaluation (Singh et al. 2023 — SciRepEval):
    intent classification, gap ranking, embedding retrieval,
    and citation reconstruction evaluated separately.

    Use --export to generate a dataset template from current DB,
    then manually review/correct labels to create ground truth.

    Use --semantic to measure hybrid gap ranking (keyword score × semantic
    similarity), requires embeddings to be configured.

    Use --analyst <citekey> to extract ground truth from a paper's PDF.

    Use --reconstruct to run citation reconstruction benchmark.

    Use --history to show past benchmark run history.

    Use --compare <id1> <id2> to compare two runs side-by-side.

    Use --export-history <path> to export run history as JSON for archival.
    """
    import json
    import subprocess
    import time

    from . import __version__
    from .evaluation import build_results_summary, load_dataset, run_all
    from .evaluation.dataset import export_dataset
    from .repositories.benchmarks import compute_dataset_hash

    kctx = _get_context(ctx)

    # --- History mode ---
    if history:
        _print_benchmark_history(kctx.state)
        return

    # --- Compare mode ---
    if compare:
        _print_benchmark_compare(kctx.state, compare[0], compare[1])
        return

    # --- Export history ---
    if export_history_path:
        runs = kctx.state.get_benchmark_runs(limit=1000)
        with open(export_history_path, "w") as f:
            json.dump(runs, f, indent=2, default=str)
        console.print(f"[green]Exported {len(runs)} runs to {export_history_path}[/green]")
        return

    # --- Candidates mode ---
    if candidates:
        from .evaluation.candidates import discover_candidates
        cands = discover_candidates(kctx.state, limit=candidates_limit)
        if not cands:
            console.print("[yellow]No benchmark candidates found (need sources with ≥3 in-library citations)[/yellow]")
            return
        t = Table(title="Benchmark Candidates")
        t.add_column("Citekey")
        t.add_column("In-lib", justify="right")
        t.add_column("Total", justify="right")
        t.add_column("Intents", justify="right")
        t.add_column("PDF")
        t.add_column("Benchmarked")
        t.add_column("Score", justify="right")
        for c in cands:
            t.add_row(
                c.citekey,
                str(c.in_library_citations),
                str(c.total_citations),
                str(c.intent_diversity),
                "[green]yes[/green]" if c.has_pdf else "[red]no[/red]",
                "[dim]yes[/dim]" if c.already_benchmarked else "no",
                f"{c.score:.0f}",
            )
        console.print(t)
        return

    # --- Prepare mode: fetch missing referenced papers ---
    if prepare_citekey:
        _run_prepare_mode(kctx, prepare_citekey)
        return

    # --- Auto mode: full autonomous pipeline ---
    if auto_mode:
        from .evaluation.pipeline import AblationParams

        ablation = None
        if any(v is not None for v in [ablation_temperature, ablation_max_recs,
                                        ablation_fragments, ablation_variant]):
            kwargs = {}
            if ablation_temperature is not None:
                kwargs["temperature"] = ablation_temperature
            if ablation_max_recs is not None:
                kwargs["max_recs_per_section"] = ablation_max_recs
            if ablation_fragments is not None:
                kwargs["fragments_per_source"] = ablation_fragments
            if ablation_variant == "fewshot":
                ablation = AblationParams.with_fewshot(**kwargs)
            else:
                ablation = AblationParams(**kwargs)

        _run_auto_mode(kctx, auto_paper, skip_prepare, ablation=ablation)
        return

    # --- Analyst mode: extract ground truth from a paper ---
    if analyst_citekey:
        _run_analyst_mode(kctx, analyst_citekey, json_output)
        return

    if export_path:
        count = export_dataset(kctx.state, Path(export_path))
        console.print(
            f"[green]Exported {count} items to {export_path}[/green]"
        )
        console.print(
            "Review and correct ground_truth labels, then run: "
            f"klemma benchmark -d {export_path}"
        )
        return

    if not dataset:
        console.print(
            "[yellow]No dataset specified. Use --dataset/-d to provide "
            "annotated ground truth, or --export to generate a template.[/yellow]"
        )
        return

    t_start = time.monotonic()
    ds = load_dataset(Path(dataset))
    recon_info = f", reconstruction: {len(ds.reconstruction.samples)} samples" if ds.reconstruction else ""
    console.print(
        f"Dataset: {len(ds.fragments)} fragments, "
        f"{len(ds.gaps)} gaps, {len(ds.similar_pairs)} similarity pairs"
        f"{recon_info}"
    )

    reranked_gaps = None
    if semantic and kctx.embeddings:
        _bsw = kctx.project.section_weights if kctx.project else None
        all_gaps = kctx.state.get_reference_gaps(limit=100, section_weights=_bsw)
        reranked_gaps = kctx.state.rerank_gaps_semantic(all_gaps, kctx.embeddings)
    elif semantic:
        console.print("[yellow]--semantic requires embeddings to be configured[/yellow]")

    # Determine effective metrics filter
    effective_metrics = "reconstruct" if reconstruct else metrics

    # Build ablation params for -d mode (same logic as --auto)
    from .evaluation.pipeline import AblationParams, compute_prompt_hash

    ablation = None
    if any(v is not None for v in [ablation_temperature, ablation_max_recs,
                                    ablation_fragments, ablation_variant]):
        kwargs = {}
        if ablation_temperature is not None:
            kwargs["temperature"] = ablation_temperature
        if ablation_max_recs is not None:
            kwargs["max_recs_per_section"] = ablation_max_recs
        if ablation_fragments is not None:
            kwargs["fragments_per_source"] = ablation_fragments
        if ablation_variant == "fewshot":
            ablation = AblationParams.with_fewshot(**kwargs)
        else:
            ablation = AblationParams(**kwargs)

    if ablation:
        params = ablation.to_snapshot()
        non_default = {k: v for k, v in params.items()
                       if v is not None and k != "prompt_variant"}
        if non_default or params.get("prompt_variant") != "default":
            console.print(f"[dim]Ablation: {params}[/dim]")

    # Initialize AI if reconstruction benchmark is requested
    ai = None
    if (effective_metrics in ("all", "reconstruct")) and ds.reconstruction:
        try:
            ai = _init_ai(kctx.config)
        except Exception:
            console.print("[dim]AI not available — reconstruction will run baseline only[/dim]")

    results = run_all(
        kctx.state, ds, effective_metrics,
        reranked_gaps=reranked_gaps, ai=ai, klemma_home=kctx.klemma_home,
        ablation=ablation,
    )

    duration = time.monotonic() - t_start

    # --- Persist run ---
    ds_hash = compute_dataset_hash(dataset)
    git_commit = ""
    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception:
        pass

    paper_citekey = ""
    if ds.reconstruction and ds.reconstruction.ground_truth:
        paper_citekey = ds.reconstruction.ground_truth.paper_citekey

    summary = build_results_summary(results)
    prompt_hash = compute_prompt_hash("reconstruct.md", kctx.klemma_home)
    effective_ablation = ablation or AblationParams()
    run_id = kctx.state.save_benchmark_run(
        dataset_path=dataset,
        dataset_hash=ds_hash,
        metrics_filter=effective_metrics,
        ai_backend=kctx.config.ai.backend,
        ai_model=kctx.config.ai.model,
        results=results,
        results_summary=summary,
        paper_citekey=paper_citekey,
        duration_seconds=round(duration, 2),
        git_commit=git_commit,
        klemma_version=__version__,
        config_snapshot={
            "ai": {"backend": kctx.config.ai.backend, "model": kctx.config.ai.model},
            "frozen_gt": True,
            "ablation": effective_ablation.to_snapshot(),
            "prompt_hash": prompt_hash,
        },
    )
    console.print(f"[dim]Run {run_id} saved ({duration:.1f}s)[/dim]")

    if json_output:
        click.echo(json.dumps(results, indent=2))
        return

    # Rich table output
    if "intent" in results:
        ir = results["intent"]
        m = ir.get("metrics", {})
        console.print(Panel(
            f"Matched: {ir['matched']}/{ir['total']} "
            f"(skipped: {ir.get('skipped', 0)})\n"
            f"[bold]Macro-F1: {m.get('macro_f1', 0):.4f}[/bold]  "
            f"Accuracy: {m.get('accuracy', 0):.4f}",
            title="Intent Classification",
        ))
        if m.get("per_class"):
            t = Table(title="Per-class metrics")
            t.add_column("Intent")
            t.add_column("Precision", justify="right")
            t.add_column("Recall", justify="right")
            t.add_column("F1", justify="right")
            t.add_column("Support", justify="right")
            for cls, vals in m["per_class"].items():
                t.add_row(
                    cls,
                    f"{vals['precision']:.4f}",
                    f"{vals['recall']:.4f}",
                    f"{vals['f1']:.4f}",
                    str(vals["support"]),
                )
            console.print(t)

    if "gaps" in results:
        gr = results["gaps"]
        gm = gr.get("metrics", {})
        gap_title = "Gap Ranking [dim](hybrid: keyword × semantic)[/dim]" if semantic else "Gap Ranking"
        console.print(Panel(
            f"Ground truth: {gr['total']} gaps, "
            f"DB gaps: {gr.get('db_gaps_count', 0)}\n"
            f"Precision@5: {gm.get('precision_at_5', 0):.4f}  "
            f"Precision@10: {gm.get('precision_at_10', 0):.4f}  "
            f"[bold]nDCG@10: {gm.get('ndcg_at_10', 0):.4f}[/bold]",
            title=gap_title,
        ))

    if "embeddings" in results:
        er = results["embeddings"]
        em = er.get("metrics", {})
        if er.get("error"):
            console.print(f"[yellow]Embeddings: {er['error']}[/yellow]")
        else:
            console.print(Panel(
                f"Queries: {er.get('evaluated', 0)}/{er['total_queries']} "
                f"(skipped: {er.get('skipped', 0)})\n"
                f"Recall@5: {em.get('avg_recall_at_5', 0):.4f}  "
                f"[bold]Recall@10: {em.get('avg_recall_at_10', 0):.4f}[/bold]  "
                f"Precision@5: {em.get('avg_precision_at_5', 0):.4f}",
                title="Embedding Retrieval",
            ))

    if "reconstruction" in results:
        _print_reconstruction_results(results["reconstruction"])


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

    console.print("\n[green]Migration complete.[/green]")
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
