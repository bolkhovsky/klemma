"""Klemma CLI — AI academic assistant."""

import re
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
from .embeddings import SemanticScholarEmbeddings, create_embeddings
from .library_provider import create_library
from .state import StateManager
from .vault import VaultAdapter

console = Console()

# CLI command → task name for model routing (used in status line)
_CMD_TASK_MAP = {
    "plan": "planner",
    "process": "extract",
    "research": "research",
    "library": "library_status",
    "ask": "ask",
    "outline": "outline_initial",
}


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
            project_chain,
            config_override=config_path,
        )
        klemma_home = project_root / ".klemma"
    elif config_path:
        # No project found, but explicit --config given — use it with system defaults
        cfg, project, project_root = resolve_effective_config(
            [],
            config_override=config_path,
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
    if cfg.obsidian.vault_path and cfg.obsidian.notes_folder:
        if not vault.check_folder(cfg.obsidian.notes_folder):
            console.print(
                f"[yellow]Warning: notes_folder '{cfg.obsidian.notes_folder}' "
                f"not found in vault. Sync and note creation will not work.[/yellow]"
            )
    library = create_library(cfg)

    # Embeddings: create provider if configured
    emb_cfg = cfg.embeddings
    emb_provider = None
    if emb_cfg.backend:
        emb_provider = create_embeddings(
            emb_cfg.model_dump(),
            api_keys=cfg.ai._resolved_api_keys or None,
        )

    # Search: create provider if configured (lazy init in `gaps suggest` otherwise)
    search_provider = None
    if cfg.search.backend:
        from .search import create_search

        search_provider = create_search(cfg.search.model_dump())

    dissertation_context = load_project_context(project_chain, cfg)
    available_tags = load_available_tags(klemma_home, cfg, project_chain=project_chain)

    return KlemmaContext(
        config=cfg,
        state=state,
        vault=vault,
        library=library,
        embeddings=emb_provider,
        search=search_provider,
        project=project,
        project_name=project_root.name,
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


BBTIndex = tuple[dict[str, str], dict[tuple[str, str], list[tuple[str, str]]]]


def build_bbt_index(entry_lookup: dict) -> BBTIndex:
    """Build lookup indexes from BBT entries for orphan resolution.

    Returns (by_item_key, by_author_year):
      - by_item_key: {item_key: citekey}
      - by_author_year: {(author_lower, year): [(citekey, item_key), ...]}
        Multiple papers by same author+year are stored as a list.
    """
    import re

    by_item_key: dict[str, str] = {}
    by_author_year: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for ck, entry in entry_lookup.items():
        if entry.item_key:
            by_item_key[entry.item_key] = ck
        am = re.match(r"([a-z.]+?)(?=[A-Z\d])", ck)
        ym = re.search(r"(\d{4})", ck)
        if am and ym:
            author = am.group(1).replace(".", "").lower()
            key = (author, ym.group(1))
            by_author_year.setdefault(key, []).append((ck, entry.item_key or ""))
    return by_item_key, by_author_year


def _unique_author_year_match(
    by_author_year: dict[tuple[str, str], list[tuple[str, str]]],
    author: str,
    year: str,
) -> tuple[str, str] | None:
    """Return a match only when exactly one candidate exists for (author, year).

    Ambiguous matches (multiple papers by same author+year) are skipped
    to prevent wrong cross-renames.
    """
    candidates = by_author_year.get((author, year))
    if candidates and len(candidates) == 1:
        return candidates[0]
    return None


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
        match = _unique_author_year_match(
            by_author_year, acq.group(1).lower(), acq.group(2)
        )
        if match:
            return match

    # Strategy 3: BBT-format "authorTitle2022a"
    clean = re.sub(r"^[a-z]\.[a-z]\.", "", old_ck)
    am = re.match(r"([a-z.]+?)(?=[A-Z\d])", clean)
    # Strip BBT disambiguation suffix (a/b/c) from year: "2024a" → "2024"
    ym = re.search(r"(\d{4})[a-z]?", old_ck)
    if am and ym:
        author = am.group(1).replace(".", "").lower()
        year = ym.group(1)
        match = _unique_author_year_match(by_author_year, author, year)
        if match and match[0] != old_ck:
            return match
    return None


def _lookup_section_type(section: str, type_lookup: dict[str, str]) -> str:
    """Find the best matching section type for a numeric section ID.

    Tries exact match first, then prefix match (longest wins).
    """
    if section in type_lookup:
        return type_lookup[section]
    # Prefix match: "2.3.1" inherits type from "2.3" or "2"
    best = ""
    for mapped_sec, mapped_type in type_lookup.items():
        if section.startswith(mapped_sec) and len(mapped_sec) > len(best):
            best = mapped_sec
    return type_lookup[best] if best else ""


def _sync_sections(ctx: KlemmaContext, quiet=False) -> dict:
    """Sync section assignments from vault frontmatter + discover new Zotero entries.

    Fast (~60ms for 138 notes). Safe to call on every command.

    IMPORTANT: Every command that reads source/gap/coverage data MUST call this
    before reading.  Otherwise users see stale gaps (e.g. already-acquired papers
    still listed as missing).  See ADR-009 in architecture-decisions.md.
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

        vault_data.append(
            {
                "citekey": citekey,
                "primary_section": str(props.get("section", "")) or None,
                "primary_chapter": chapter,
                "sections": (
                    [str(s) for s in sections_list]
                    if isinstance(sections_list, list)
                    else []
                ),
                "chapters": (
                    [int(c) for c in chapters_list]
                    if isinstance(chapters_list, list)
                    else []
                ),
                "quality": quality or 0,
                "priority": props.get("priority", "medium"),
                "nr1": props.get("relevance_nr1", 0) or 0,
                "nr2": props.get("relevance_nr2", 0) or 0,
                "note_path": f"{notes_folder}/{note_name}.md",
            }
        )

    # 2. Discover new Zotero entries not in DB + detect renames
    # Determine auto-register mode: "none" (paper), "mapped" (filter by chapter_mapping), "all"
    project = ctx.project
    if project and project.type == "paper":
        auto_register_mode = "none"
    elif project and project.auto_register == "mapped":
        auto_register_mode = "mapped"
    else:
        auto_register_mode = "all"

    # Load existing DB source IDs (needed for paper filtering + new entry detection)
    existing = state.get_existing_source_ids()

    new_entries = []
    renames = []
    skipped_irrelevant = 0
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
            if auto_register_mode != "none" and citekey not in vault_citekeys:
                classification = auto_classify(entry, cfg, project=project)
                if auto_register_mode == "mapped" and not classification.get("matched"):
                    skipped_irrelevant += 1
                    continue
                new_entries.append((citekey, classification))

        # Backfill zotero_key BEFORE orphan detection so itemKey-based
        # renames (first loop above) catch most cases on subsequent runs,
        # reducing reliance on fuzzy matching.
        backfill = {
            ck: entry.item_key for ck, entry in entry_lookup.items() if entry.item_key
        }
        if backfill:
            state.populate_zotero_keys(backfill)

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

    # 3. Sync to DB
    # Papers: only sync vault notes for sources already registered in DB
    if auto_register_mode == "none":
        vault_data = [vd for vd in vault_data if vd["citekey"] in existing]

    # Filter out vault notes that match old (renamed-from) citekeys —
    # prevents sync_source_sections from re-creating the old source row.
    renamed_from = {old_ck for old_ck, _ in renames}
    if renamed_from:
        vault_data = [vd for vd in vault_data if vd["citekey"] not in renamed_from]

    result = state.sync_source_sections(vault_data, new_entries)

    # Backfill metadata (title/authors/year/abstract/doi) from library for sources missing it
    if ctx.library:
        entry_lookup = ctx.library.entries
        missing = state.get_sources_missing_title()
        backfilled = 0
        for source_id in missing:
            entry = entry_lookup.get(source_id)
            if not entry:
                continue
            state.update_source_info(
                source_id,
                title=entry.title or "",
                authors=entry.authors_str or "",
                year=entry.year,
                abstract=entry.abstract or "",
                doi=entry.DOI or "",
            )
            backfilled += 1
        result["metadata_backfilled"] = backfilled

    # Sync section type mappings (backfill section_type columns)
    project = ctx.project
    if project and (project.chapters or project.section_type_map):
        st_result = state.sync_section_types(project)
        result["section_types_updated"] = st_result["updated"]
        result["section_types_unmapped"] = st_result["unmapped"]

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
        if skipped_irrelevant:
            parts.append(
                f"[yellow]{skipped_irrelevant} skipped (no chapter_mapping match)[/yellow]"
            )
        if parts:
            console.print("[dim]Sync:[/dim] " + " | ".join(parts))

    return result


def _print_status_line(
    state: StateManager, project_name: str = "default", model: str = ""
):
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
            parts.insert(
                1 if project_name != "default" else 0, f"[magenta]{model}[/magenta]"
            )
        gap_summary = state.get_gap_summary()
        if gap_summary["open_count"] > 0:
            top = ""
            if gap_summary["top_ref"]:
                top = f" (top: {gap_summary['top_ref']} x{gap_summary['top_count']})"
            parts.append(f"[yellow]{gap_summary['open_count']} ref-gaps{top}[/yellow]")
        prune = state.get_prune_summary()
        if prune["total"] > 0:
            parts.append(
                f"[yellow]{prune['total']} pruned ({prune['drop']} drop, {prune['maybe']} maybe)[/yellow]"
            )
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
        actions.append(
            (
                f"{pending} sources pending extraction",
                "klemma process",
            )
        )
    if failed > 0:
        actions.append(
            (
                f"{failed} failed sources to retry",
                "klemma process --retry",
            )
        )

    # 2. Embedding coverage < 100%
    if emb_stats:
        total = emb_stats.get("total", 0)
        embedded = emb_stats.get("embedded", 0)
        remaining = total - embedded
        if remaining > 0:
            actions.append(
                (
                    f"{remaining} sources missing embeddings ({embedded}/{total})",
                    "klemma embed",
                )
            )

    # 3. Top coverage gaps → research
    if gaps_data:
        top_gap = gaps_data[0]
        actions.append(
            (
                f"section {top_gap['section']} has only {top_gap['count']} sources",
                f"klemma research -s {top_gap['section']}",
            )
        )

    # 4. Top ref gaps → suggest acquisitions
    if ref_gaps:
        top = ref_gaps[0]
        top_authors = (top.get("ref_authors") or "").strip()[:30]
        top_count = top.get("count", 0)
        n_gaps = len(ref_gaps)
        actions.append(
            (
                f"{n_gaps} open ref gaps (top: {top_authors}, cited x{top_count})",
                "klemma gaps suggest",
            )
        )

    # 5. Prune verdicts pending review
    if prune_summary.get("total", 0) > 0:
        drop = prune_summary.get("drop", 0)
        maybe = prune_summary.get("maybe", 0)
        actions.append(
            (
                f"{drop} drop + {maybe} maybe prune verdicts pending",
                "klemma library prune --list",
            )
        )

    if not actions:
        return

    console.print()
    console.print("[bold]Recommended Actions[/bold]")
    for i, (reason, cmd) in enumerate(actions, 1):
        console.print(f"  [dim]{i}.[/dim] {reason}")
        console.print(f"     [green]$ {cmd}[/green]")


def _print_ref_gaps_table(
    state: StateManager,
    limit: int = 20,
    embeddings=None,
    section_weights: dict[str, float] | None = None,
):
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
        show_edge=False,
        pad_edge=False,
    )
    ref_table.add_column("#", justify="right", style="dim", width=3)
    ref_table.add_column("Score", justify="right", width=6)
    ref_table.add_column("Count", justify="right", width=5)
    ref_table.add_column("Authors", width=20)
    ref_table.add_column("Year", width=5)
    ref_table.add_column("Title")
    ref_table.add_column("Why", max_width=30, style="dim")

    for i, g in enumerate(ref_gaps, 1):
        score_style = (
            "red bold" if g["score"] >= 10 else "yellow" if g["score"] >= 5 else "dim"
        )
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
@click.option(
    "--config", "-c", default=None, help="Config file path (override project config)"
)
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
            # Resolve effective model: task-specific override > default
            effective_model = kctx.config.ai.model
            task = _CMD_TASK_MAP.get(ctx.invoked_subcommand)
            if task:
                from .ai import resolve_task_model

                override = resolve_task_model(task, kctx.config.ai)
                if override:
                    effective_model = override
            _print_status_line(
                kctx.state, project_name=kctx.project_name, model=effective_model
            )
        except Exception:
            pass

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


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
    help="Path to dissertation plan-prospect .docx — auto-fills project from it",
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

    \b
    Examples:
      klemma init                                  # interactive setup
      klemma init --plan plan.docx                 # from dissertation plan
      klemma init --backend claude                 # Claude Code Max, S2 embeddings
      klemma init --backend litellm --api-key sk-  # OpenAI LLM + embeddings
      klemma init --force                          # re-run wizard
    """
    from .setup import InitValues, init_project, init_system

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
        from .plan_parser import parse as parse_plan

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

    # Detect parent project before init — affects config defaults (ADR-012)
    pre_chain = discover_project_chain(project_dir.parent)
    _has_parent = len(pre_chain) > 0

    result = init_project(
        project_dir, project_type=project_type, values=values, has_parent=_has_parent
    )

    # Overwrite KLEMMA.md with rich plan content if plan was provided
    effective_plan = plan_data or (values.plan_data if values else None)
    if effective_plan:
        from .plan_parser import to_klemma_md as plan_to_klemma_md

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
            from .config import update_project_config

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
            from .config import scan_project_files
            from .skills.outliner import generate_outline as gen_outline
            from .skills.outliner import save_outline

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
    embeddings = (
        raw.get("embeddings", {}) if isinstance(raw.get("embeddings"), dict) else {}
    )

    # Check klemmarc for existing OpenAI key (for prefilling "do you have a key?" default)
    has_klemmarc_openai_key = False
    try:
        from .setup import _find_klemmarc

        klemmarc = _find_klemmarc(Path.home())
        if klemmarc:
            import yaml as _y2

            krc = _y2.safe_load(klemmarc.read_text(encoding="utf-8")) or {}
            has_klemmarc_openai_key = bool(krc.get("api_keys", {}).get("openai"))
    except Exception:
        pass

    return {
        "project_type": project.get("type", "dissertation"),
        "title": project.get("title", ""),
        "description": project.get("description", ""),
        "keywords": project.get("priority_terms", []),
        "language": ai.get("language", "ru"),
        "backend": ai.get("backend", ""),
        "openai_api_key": has_klemmarc_openai_key,  # bool for default, not the actual key
        "embeddings_backend": embeddings.get("backend", ""),
        "vault_path": obsidian.get("vault_path", ""),
        "notes_folder": obsidian.get("notes_folder", ""),
        "tags_folder": obsidian.get("tags_folder", ""),
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
    click.echo(
        f"  Found {len(matches)} matching sources (of {total_in_vault} in vault):"
    )

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
        discover_bbt_json,
        discover_obsidian_vault,
        discover_zotero_storage,
    )
    from .setup import InitValues

    pf = prefill or {}

    click.echo("\nKlemma project setup\n")

    # --- Plan-prospect (first question) ---
    plan_data = pf.get("_plan_data")  # set when --plan was passed
    if not plan_data:
        plan_path_str = click.prompt(
            "  Dissertation plan (.docx) — path or empty to skip",
            default="",
            show_default=False,
        )
        if plan_path_str:
            plan_file = Path(plan_path_str.strip())
            if plan_file.exists():
                try:
                    from .plan_parser import parse as parse_plan

                    plan_data = parse_plan(plan_file)
                    click.echo(f"    Parsed: {plan_data.title[:70]}...")
                    click.echo(
                        f"    Chapters: {len(plan_data.chapters)}, "
                        f"НР: {len(plan_data.results)}, "
                        f"Tasks: {len(plan_data.tasks)}"
                    )
                except ImportError:
                    click.echo(
                        "    [warning] python-docx not installed: pip install python-docx"
                    )
                except Exception as e:
                    click.echo(f"    [warning] Could not parse: {e}")
            else:
                click.echo(f"    [warning] File not found: {plan_file}")

    # --- Project basics (pre-filled from plan if available) ---
    if plan_data:
        project_type = "dissertation"
        title = plan_data.title
        click.echo(f"\n  Project type: {project_type}")
        click.echo(f"  Title: {title[:80]}...")
    else:
        project_type = click.prompt(
            "  Project type",
            type=click.Choice(
                ["dissertation", "paper", "thesis"], case_sensitive=False
            ),
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
    if project_type == "paper" and not plan_data:
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
        default=pf.get("language", "ru"),
    )

    # --- AI setup ---
    # Step 1: OpenAI key (needed for embeddings; optionally for LLM too)
    click.echo("\n  AI setup")
    openai_api_key = ""
    has_openai = click.confirm(
        "  Do you have an OpenAI API key? (needed for embeddings)",
        default=bool(pf.get("openai_api_key")),
    )
    if has_openai:
        openai_api_key = click.prompt("  OpenAI API key", hide_input=True)
        if not openai_api_key.startswith("sk-"):
            click.echo("    [warning] Key doesn't start with sk- — saving anyway")

    # Step 2: LLM backend
    backend = ""
    ai_model = ""
    if has_openai:
        click.echo("\n  LLM backend")
        click.echo("    1. Claude Code Max (free — uses claude CLI)")
        click.echo("    2. OpenAI (uses the key above)")

        prefill_backend = pf.get("backend", "")
        llm_default = "2" if prefill_backend == "litellm" else "1"

        llm_choice = click.prompt(
            "  Choose",
            type=click.Choice(["1", "2"]),
            default=llm_default,
        )
        if llm_choice == "1":
            backend = "claude"
            ai_model = "sonnet"
            click.echo("    LLM: Claude Code Max  |  Embeddings: OpenAI")
        else:
            backend = "litellm"
            ai_model = "openai/gpt-4.1"
            click.echo("    LLM: OpenAI gpt-4.1  |  Embeddings: OpenAI")
    else:
        backend = "claude"
        ai_model = "sonnet"
        click.echo("    LLM: Claude Code Max  |  Embeddings: not configured (add later)")

    embeddings_backend = "openai" if has_openai else ""

    # --- Auto-discovery (prefill overrides discovery) ---
    click.echo("\n  Detecting paths...")

    values = InitValues(
        project_type=project_type,
        title=title,
        description=description,
        keywords=keywords,
        language=language,
        backend=backend,
        ai_model=ai_model,
        openai_api_key=openai_api_key,
        embeddings_backend=embeddings_backend,
        plan_data=plan_data,
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

    if values.vault_path:
        from .discovery import discover_vault_folders
        auto_notes, auto_tags = discover_vault_folders(Path(values.vault_path))
        if auto_notes and not values.notes_folder:
            values.notes_folder = auto_notes
        if auto_tags and not values.tags_folder:
            values.tags_folder = auto_tags
        if values.notes_folder:
            notes_dir = Path(values.vault_path) / values.notes_folder
            if not notes_dir.is_dir():
                console.print(
                    f"[yellow]  Warning: '{values.notes_folder}' not found in vault.[/yellow]"
                )
                console.print(
                    "[dim]  Fix notes_folder in .klemma/config.yaml after init.[/dim]"
                )

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
        if not click.confirm("    Use this path?", default=True):
            bbt_str = click.prompt("    BBT JSON export path", default="")
            values.zotero_library_json = bbt_str
        else:
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
            cfg,
            state,
            vault,
            ai,
            project=kctx.project,
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
    console.print(
        Panel(
            f"[bold]{plan.focus}[/bold]\n\n" f"[dim]Почему:[/dim] {plan.why}",
            title="Фокус сегодня",
            border_style="green",
        )
    )

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
@click.option(
    "--force",
    is_flag=True,
    help="Reprocess completed sources, replacing existing fragments",
)
@click.option(
    "--model", default=None, help="Override AI model (e.g. openai/gpt-4.1-mini)"
)
@click.option(
    "--no-embed", is_flag=True, help="Skip auto-embedding after processing"
)
@click.pass_context
def process(ctx, citekeys, serial, force, model, no_embed):
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
        console.print(
            f"[blue]Reprocessing {len(keys)} completed sources (replacing fragments)[/blue]"
        )
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
                    pool.submit(
                        _process_single,
                        ck,
                        cfg,
                        state,
                        vault,
                        ai,
                        pdf_extractor,
                        kctx.library,
                        quiet=True,
                        dissertation_context=kctx.dissertation_context,
                        available_tags=kctx.available_tags,
                        klemma_home=kctx.klemma_home,
                        embeddings=kctx.embeddings,
                        force=force,
                        no_embed=no_embed,
                    ): ck
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
        newly_skipped = 0
        for idx, ck in enumerate(keys, 1):
            n_frags, status = results.get(ck, (0, "unknown"))
            if n_frags > 0:
                console.print(
                    f"  [{idx}/{len(keys)}] @{ck} — [green]{n_frags} fragments[/green]"
                )
                ok += 1
            else:
                console.print(f"  [{idx}/{len(keys)}] @{ck} — [red]{status}[/red]")
                if status in ("PDF not found", "text too short", "no fragments"):
                    newly_skipped += 1
        skip_msg = (
            f" {newly_skipped} skipped (no PDF / text too short)."
            if newly_skipped
            else ""
        )
        console.print(
            f"\n[green]Done: {ok}/{len(keys)} processed (parallel, {elapsed:.0f}s).[/green]{skip_msg}"
        )
    else:
        processed = 0
        newly_skipped = 0
        for idx, ck in enumerate(keys, 1):
            if len(keys) > 1:
                console.print(f"\n[bold][{idx}/{len(keys)}] {ck}[/bold]")
            n_frags, reason = _process_single(
                ck,
                cfg,
                state,
                vault,
                ai,
                pdf_extractor,
                kctx.library,
                dissertation_context=kctx.dissertation_context,
                available_tags=kctx.available_tags,
                klemma_home=kctx.klemma_home,
                project_type=kctx.project.type if kctx.project else "dissertation",
                embeddings=kctx.embeddings,
                force=force,
                no_embed=no_embed,
            )
            if n_frags > 0:
                processed += 1
            elif reason in ("PDF not found", "text too short", "no fragments"):
                newly_skipped += 1
        if len(keys) > 1:
            skip_msg = (
                f" {newly_skipped} skipped (no PDF / text too short)."
                if newly_skipped
                else ""
            )
            console.print(
                f"\n[green]Done: {processed}/{len(keys)} processed.[/green]{skip_msg}"
            )

    # DEV mode: show benchmark candidate hints
    if kctx.config.instance.dev_mode:
        from .evaluation.candidates import discover_candidates, format_candidate_hint

        candidates = discover_candidates(kctx.state, limit=3)
        hint = format_candidate_hint(candidates)
        if hint:
            console.print(hint)


def _auto_embed_after_process(
    citekey,
    state,
    embeddings,
    quiet=False,
):
    """Embed fragments + recompute section centroids for a just-processed source.

    Returns total embeddings created.
    """
    count = 0

    # Fragment embeddings
    fragments = state.get_fragments(source_id=citekey)
    for frag in fragments:
        if frag.get("embedding"):  # already embedded
            continue
        try:
            vec = embeddings.embed(frag["fragment_text"])
            if vec:
                state.save_fragment_embedding(frag["id"], vec, embeddings.model_name)
                count += 1
        except Exception:
            pass

    if count and not quiet:
        console.print(f"  [dim]embedded {count} fragments[/dim]")

    # Section centroid recomputation for sections this source belongs to
    model_name = embeddings.model_name
    all_emb = state.get_all_embeddings(model=model_name)
    if all_emb:
        with state._conn() as conn:
            cur = conn.execute(
                "SELECT DISTINCT section FROM source_sections WHERE source_id=?",
                (citekey,),
            )
            source_sections = [row["section"] for row in cur.fetchall()]

        sections_updated = 0
        for sec in source_sections:
            source_ids = state.get_section_sources(sec)
            vecs = [all_emb[sid] for sid in source_ids if sid in all_emb]
            if not vecs:
                continue
            dim = len(vecs[0])
            centroid = [sum(v[i] for v in vecs) / len(vecs) for i in range(dim)]
            state.save_section_embedding(sec, centroid, model_name, len(vecs))
            sections_updated += 1

        if sections_updated and not quiet:
            console.print(f"  [dim]updated {sections_updated} section centroids[/dim]")

    return count


def _process_single(
    citekey,
    cfg,
    state,
    vault,
    ai,
    pdf_extractor,
    library,
    quiet=False,
    dissertation_context="",
    available_tags=None,
    klemma_home=None,
    project_type="dissertation",
    embeddings=None,
    force=False,
    no_embed=False,
):
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
        from .literature.models import Author, ZoteroEntry

        # Fall back to DB metadata from acquire (title, authors, year)
        db_title = source.get("title", "") if source else ""
        db_authors = source.get("authors", "") if source else ""
        db_year = source.get("year") if source else None
        db_abstract = source.get("abstract", "") if source else ""
        issued = {"date-parts": [[db_year]]} if db_year else None
        authors = (
            [Author(literal=a.strip()) for a in db_authors.split(",") if a.strip()]
            if db_authors
            else []
        )
        entry = ZoteroEntry(
            id=citekey,
            title=db_title or citekey,
            abstractNote=db_abstract,
            author=authors,
            issued=issued,
        )

    if not quiet:
        console.print(
            f"[blue]Processing: {entry.authors_str} ({entry.year or '?'})[/blue] [dim]@{citekey}[/dim]"
        )

    # Find PDF
    pdf_search_paths = [Path(cfg.zotero.storage_path)]
    pdf_path = pdf_extractor.find_pdf(
        citekey,
        pdf_search_paths,
        entry_title=entry.title or "",
        direct_path=source.get("pdf_path") if source else entry.pdf_path,
        pdf_lookup=library.pdf_paths,
    )

    if not pdf_path:
        if not quiet:
            console.print("  [red]PDF not found[/red]")
        state.sources.mark_skipped(citekey, "PDF not found")
        return (0, "PDF not found")

    # Extract text
    pdf_text = pdf_extractor.extract(pdf_path)
    if not pdf_text or len(pdf_text) < cfg.processing.min_pdf_length:
        if not quiet:
            console.print("  [red]PDF extraction failed or text too short[/red]")
        state.sources.mark_skipped(citekey, "text too short")
        return (0, "text too short")

    # If reprocessing, clear old fragments before extracting fresh ones
    if force:
        state.delete_fragments(citekey)

    # Extract fragments
    result = extract_fragments(
        entry,
        pdf_text,
        cfg,
        state,
        ai,
        dissertation_context=dissertation_context,
        available_tags=available_tags,
        klemma_home=klemma_home,
        project_type=project_type,
    )

    if not result or not result.fragments:
        if not quiet:
            console.print("  [red]No fragments extracted[/red]")
        state.sources.mark_skipped(citekey, "no fragments")
        return (0, "no fragments")

    if not quiet:
        console.print(f"  [green]{len(result.fragments)} fragments[/green]", end="")

    # Save to vault
    saved_path = save_fragments_to_vault(
        citekey,
        result.fragments,
        vault,
        entry=entry,
        config=cfg,
        state=state,
        pdf_text=pdf_text,
        ai=ai,
        entry_lookup=library.entries,
        dissertation_context=dissertation_context,
        available_tags=available_tags,
        klemma_home=klemma_home,
    )
    if not quiet:
        if saved_path:
            console.print(f" → @{citekey}")
        else:
            console.print(" [dim](DB only)[/dim]")

    # Backfill abstract from S2 if missing (e.g. acquire hit rate limit)
    abstract = entry.abstract or ""
    if not abstract and entry.title:
        try:
            from .literature.metadata import lookup_s2

            hit = lookup_s2(entry.title)
            if hit and hit.get("abstract"):
                abstract = hit["abstract"]
                state.update_source_info(citekey, abstract=abstract)
                if not quiet:
                    console.print("  [dim]abstract backfilled from S2[/dim]")
        except Exception:
            pass

    # Auto-embed if provider available and not suppressed
    if embeddings and not no_embed:
        # Source embedding (title + abstract)
        if abstract:
            try:
                vec = embeddings.embed(entry.title or citekey, abstract)
                if vec:
                    state.save_embedding(citekey, vec, embeddings.model_name)
                    if not quiet:
                        console.print(f"  [dim]embedded ({embeddings.model_name})[/dim]")
            except Exception as e:
                if not quiet:
                    console.print(f"  [dim]embed failed: {e}[/dim]")

        # Fragment embeddings + section centroids
        _auto_embed_after_process(citekey, state, embeddings, quiet=quiet)

    return (len(result.fragments), "ok")


@main.group(invoke_without_command=True, name="embed")
@click.pass_context
def embed(ctx):
    """Compute and store embeddings.

    Subcommands:
      klemma embed sources    — embed sources (default)
      klemma embed fragments  — embed fragment text
      klemma embed sections   — compute section centroid embeddings
      klemma embed all        — run sources → fragments → sections in sequence

    Run `klemma embed sources --help` for source-embedding options.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(embed_sources)


def _resolve_emb(kctx, backend, dry_run):
    """Resolve embedding provider from context or --backend override."""
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
        return None
    return emb


@embed.command(name="sources")
@click.argument("citekeys", required=False, nargs=-1)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show how many would be embedded without calling API",
)
@click.option(
    "--backend",
    type=click.Choice(["s2", "local", "openai"]),
    help="Override embedding backend",
)
@click.option(
    "--backfill", is_flag=True, help="Fetch missing abstracts from S2 before embedding"
)
@click.pass_context
def embed_sources(ctx, citekeys, dry_run, backend, backfill):
    """Embed source title+abstract vectors.

    Without CITEKEYS: embed all sources missing embeddings.
    With CITEKEYS: embed specific sources by citekey.
    """
    kctx = _get_context(ctx)
    state = kctx.state
    emb = _resolve_emb(kctx, backend, dry_run)
    if emb is None:
        return

    # Get candidates
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
        candidates = state.get_sources_without_embeddings()

    if not candidates:
        console.print("[green]All sources already have embeddings.[/green]")
        return

    is_s2 = isinstance(emb, SemanticScholarEmbeddings) if emb else False
    entries = kctx.library.entries if kctx.library else {}

    # --backfill: fetch missing abstracts from S2 before embedding
    if backfill and not is_s2:
        from .literature.metadata import lookup_s2

        no_abs = [c for c in candidates if not (entries.get(c) and entries[c].abstract)]
        if no_abs:
            from rich.progress import Progress

            filled = 0
            with Progress(console=console) as progress:
                btask = progress.add_task(
                    "Backfilling abstracts from S2...", total=len(no_abs)
                )
                for ck in no_abs:
                    entry = entries.get(ck)
                    title = entry.title if entry else ck
                    if title and title != ck:
                        hit = lookup_s2(title)
                        if hit and hit.get("abstract"):
                            state.update_source_info(ck, abstract=hit["abstract"])
                            if entry:
                                entry.abstract = hit["abstract"]
                            filled += 1
                    progress.advance(btask)
            if filled:
                console.print(
                    f"[green]Backfilled {filled}/{len(no_abs)} abstracts.[/green]"
                )

    if dry_run:
        console.print(f"[blue]Would embed {len(candidates)} sources[/blue]")
        return

    embedded = 0
    api_miss = 0
    failed = 0

    from rich.progress import Progress

    with Progress(console=console) as progress:
        task = progress.add_task("Embedding sources...", total=len(candidates))
        for ck in candidates:
            entry = entries.get(ck)
            title = entry.title if entry else ck
            abstract = entry.abstract if entry else ""
            if not is_s2 and not abstract:
                frags = state.get_fragments(source_id=ck, limit=10)
                frag_text = " ".join(
                    f["fragment_text"] for f in frags if f.get("fragment_text")
                )
                abstract = frag_text[:2000] if frag_text else ""
            try:
                vec = emb.embed(title, abstract)
                if vec:
                    state.save_embedding(ck, vec, emb.model_name)
                    embedded += 1
                else:
                    api_miss += 1
            except Exception as e:
                console.print(f"  [red]{ck}: {e}[/red]")
                failed += 1
            progress.advance(task)

    emb_stats = state.get_embedding_stats()
    parts = [f"[green]Embedded: {embedded}[/green]"]
    if api_miss:
        parts.append(f"[yellow]Not found: {api_miss}[/yellow]")
    if failed:
        parts.append(f"[red]Failed: {failed}[/red]")
    parts.append(f"[dim]Total: {emb_stats['embedded']}/{emb_stats['total']}[/dim]")
    console.print("\n" + " | ".join(parts))


@embed.command(name="fragments")
@click.option("--dry-run", is_flag=True, help="Preview without API calls")
@click.option(
    "--backend",
    type=click.Choice(["s2", "local", "openai"]),
    help="Override embedding backend",
)
@click.pass_context
def embed_fragments(ctx, dry_run, backend):
    """Embed extracted fragment text vectors."""
    kctx = _get_context(ctx)
    state = kctx.state
    emb = _resolve_emb(kctx, backend, dry_run)
    if emb is None:
        return

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


@embed.command(name="sections")
@click.option("--dry-run", is_flag=True, help="Preview without writing to DB")
@click.option(
    "--backend",
    type=click.Choice(["s2", "local", "openai"]),
    help="Override embedding backend",
)
@click.pass_context
def embed_sections(ctx, dry_run, backend):
    """Compute section centroid embeddings from source vectors."""
    kctx = _get_context(ctx)
    state = kctx.state
    emb = _resolve_emb(kctx, backend, dry_run)
    if emb is None:
        return

    model_name = emb.model_name if emb else None
    all_emb = state.get_all_embeddings(model=model_name)
    if not all_emb:
        console.print(
            "[yellow]No source embeddings found. Run `klemma embed sources` first.[/yellow]"
        )
        return

    with state._conn() as conn:
        cur = conn.execute(
            "SELECT DISTINCT section FROM source_sections ORDER BY section"
        )
        all_sections = [row["section"] for row in cur.fetchall()]

    embedded = 0
    skipped = 0
    for sec in all_sections:
        source_ids = state.get_section_sources(sec)
        vecs = [all_emb[sid] for sid in source_ids if sid in all_emb]
        if not vecs:
            skipped += 1
            continue
        dim = len(vecs[0])
        centroid = [sum(v[i] for v in vecs) / len(vecs) for i in range(dim)]
        if not dry_run:
            state.save_section_embedding(sec, centroid, model_name or "unknown", len(vecs))
        embedded += 1

    if dry_run:
        console.print(
            f"[blue]Would embed {embedded} sections ({skipped} have no source embeddings)[/blue]"
        )
    else:
        console.print(f"[green]Section embeddings: {embedded} computed[/green]", end="")
        if skipped:
            console.print(
                f" | [yellow]{skipped} skipped (no source embeddings)[/yellow]",
                end="",
            )
        console.print()


@embed.command(name="all")
@click.option("--dry-run", is_flag=True, help="Preview without API calls")
@click.option(
    "--backend",
    type=click.Choice(["s2", "local", "openai"]),
    help="Override embedding backend",
)
@click.pass_context
def embed_all(ctx, dry_run, backend):
    """Run sources → fragments → sections in sequence."""
    console.print("[dim]Step 1/3: sources[/dim]")
    ctx.invoke(embed_sources, dry_run=dry_run, backend=backend)
    console.print("\n[dim]Step 2/3: fragments[/dim]")
    ctx.invoke(embed_fragments, dry_run=dry_run, backend=backend)
    console.print("\n[dim]Step 3/3: sections[/dim]")
    ctx.invoke(embed_sections, dry_run=dry_run, backend=backend)


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
            console.print("[red]No embeddings found. Run `klemma embed` first.[/red]")
            return
        model_name = None
    else:
        model_name = emb.model_name
        all_emb = state.get_all_embeddings(model=model_name)
        if not all_emb:
            console.print("[red]No embeddings found. Run `klemma embed` first.[/red]")
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
            sum(v[i] for v in section_vecs) / len(section_vecs) for i in range(dim)
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
                    console.print(
                        f"[red]No embedding for @{arg} and no abstract to embed[/red]"
                    )
                    return
            else:
                console.print(
                    f"[red]No embedding for @{arg}. Run `klemma embed {arg}` first.[/red]"
                )
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
        cross = [
            (sid, sim)
            for sid, sim in sims[:top_k]
            if not any(sid in state.get_section_sources(arg))
        ]
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
    skipped = proc_stats.get("skipped", 0)
    total = proc_stats.get("total", 0)
    parts = [f"[green]{completed} completed[/green]"]
    if skipped:
        parts.append(f"[dim]{skipped} skipped[/dim]")
    if pending:
        parts.append(f"[yellow]{pending} pending[/yellow]")
    if failed:
        parts.append(f"[red]{failed} failed[/red]")
    console.print(
        f"Processing: {' | '.join(parts)}  [dim]({total} total, {frag_stats.get('total', 0)} fragments)[/dim]"
    )
    console.print()

    # --- Coverage (chapter-based for dissertation/thesis, simple for paper) ---
    has_chapters = project and project.chapters and project.type != "paper"

    if has_chapters:
        chapter_numbers = project.chapter_numbers
        type_lookup = cov.get("section_type_lookup", {})
        table = Table(title="Coverage by Chapter", show_edge=False, pad_edge=False)
        table.add_column("Chapter", style="cyan")
        table.add_column("Type", style="dim")
        table.add_column("Sources", justify="right", width=8)
        for ch in chapter_numbers:
            if chapter and ch != chapter:
                continue
            count = cov["chapters"].get(ch, 0)
            style = "green" if count >= 10 else "yellow" if count >= 5 else "red"
            name = project.chapters.get(ch, "")
            stype = type_lookup.get(str(ch), "")
            table.add_row(f"Ch {ch}: {name}", stype, f"[{style}]{count}[/{style}]")
        console.print(table)

        # Sections (verbose or filtered by chapter)
        if (verbose or chapter) and cov["sections"]:
            console.print()
            type_lookup = cov.get("section_type_lookup", {})
            sec_table = Table(
                title="Coverage by Section", show_edge=False, pad_edge=False
            )
            sec_table.add_column("Section", style="cyan")
            sec_table.add_column("Type", style="dim")
            sec_table.add_column("Sources", justify="right", width=8)
            for sec, count in sorted(cov["sections"].items()):
                if chapter and not sec.startswith(f"{chapter}."):
                    continue
                style = "green" if count >= 3 else "yellow" if count >= 1 else "red"
                stype = _lookup_section_type(sec, type_lookup)
                sec_table.add_row(sec, stype, f"[{style}]{count}[/{style}]")
            console.print(sec_table)
    elif not project or project.type == "paper":
        # Paper: show section coverage if any, no chapter structure
        if cov["sections"]:
            type_lookup = cov.get("section_type_lookup", {})
            sec_table = Table(
                title="Coverage by Section", show_edge=False, pad_edge=False
            )
            sec_table.add_column("Section", style="cyan")
            sec_table.add_column("Type", style="dim")
            sec_table.add_column("Sources", justify="right", width=8)
            for sec, count in sorted(cov["sections"].items()):
                style = "green" if count >= 3 else "yellow" if count >= 1 else "red"
                stype = _lookup_section_type(sec, type_lookup)
                sec_table.add_row(sec, stype, f"[{style}]{count}[/{style}]")
            console.print(sec_table)
    else:
        # Fallback: legacy dissertation config
        chapter_numbers = list(range(1, 5))
        type_lookup = cov.get("section_type_lookup", {})
        table = Table(title="Coverage by Chapter", show_edge=False, pad_edge=False)
        table.add_column("Chapter", style="cyan")
        table.add_column("Type", style="dim")
        table.add_column("Sources", justify="right", width=8)
        for ch in chapter_numbers:
            count = cov["chapters"].get(ch, 0)
            style = "green" if count >= 10 else "yellow" if count >= 5 else "red"
            name = cfg.dissertation.chapters.get(ch, "")
            stype = type_lookup.get(str(ch), "")
            table.add_row(f"Ch {ch}: {name}", stype, f"[{style}]{count}[/{style}]")
        console.print(table)

    # --- Coverage by semantic type ---
    section_types = cov.get("section_types", {})
    if section_types:
        console.print()
        type_parts = []
        for st_name, st_count in sorted(section_types.items()):
            style = "green" if st_count >= 10 else "yellow" if st_count >= 5 else "dim"
            type_parts.append(f"[{style}]{st_name} {st_count}[/{style}]")
        console.print(f"[bold]By type:[/bold] {' | '.join(type_parts)}")

    # --- Top gaps ---
    min_sources = (
        project.min_sources_per_section
        if project
        else cfg.dissertation.min_sources_per_section
    )
    gaps_data = state.get_gaps(min_sources=min_sources)
    if gaps_data:
        if chapter:
            gaps_data = [g for g in gaps_data if g["section"].startswith(f"{chapter}.")]
        shown = gaps_data if verbose else gaps_data[:5]
        console.print()
        console.print(
            f"[bold]Top Gaps[/bold] [dim](sections with < {min_sources} sources)[/dim]"
        )
        for gap in shown:
            needed = min_sources - gap["count"]
            console.print(
                f"  [red]{gap['section']}[/red] — {gap['count']} sources [dim](need {needed} more)[/dim]"
            )
        if not verbose and len(gaps_data) > 5:
            console.print(
                f"  [dim]... and {len(gaps_data) - 5} more (use --verbose)[/dim]"
            )

    # --- Reference gaps ---
    _sw = kctx.project.section_weights if kctx.project else None
    if verbose:
        _print_ref_gaps_table(
            state, limit=20, embeddings=kctx.embeddings, section_weights=_sw
        )
    else:
        ref_gaps = state.get_reference_gaps(limit=5, section_weights=_sw)
        if ref_gaps:
            gap_summary = state.get_gap_summary()
            console.print()
            console.print(
                f"[bold]Ref Gaps[/bold] [dim]({gap_summary['open_count']} open)[/dim]"
            )
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
                    (
                        str(d["result_comparison"])
                        if d["result_comparison"]
                        else "[dim]0[/dim]"
                    ),
                    str(d["total"]),
                )
            console.print(it)

    # --- Verbose: embedding stats ---
    if verbose:
        emb_stats = state.get_embedding_stats()
        if emb_stats["embedded"] > 0 or emb_stats["total"] > 0:
            console.print()
            pct = (
                (emb_stats["embedded"] / emb_stats["total"] * 100)
                if emb_stats["total"]
                else 0
            )
            console.print(
                f"[bold]Embeddings[/bold]: {emb_stats['embedded']}/{emb_stats['total']} "
                f"sources ({pct:.0f}%)"
            )
            for model, cnt in emb_stats["models"].items():
                console.print(f"  [dim]{model}: {cnt}[/dim]")

            # Section embeddings
            sec_stats = state.get_section_embedding_stats()
            if sec_stats["total_sections"] > 0:
                sec_emb = sec_stats["embedded_sections"]
                sec_total = sec_stats["total_sections"]
                sec_pct = sec_emb / sec_total * 100
                missing = sec_total - sec_emb
                line = (
                    f"[bold]Section embeddings[/bold]: {sec_emb}/{sec_total} "
                    f"sections ({sec_pct:.0f}%)"
                )
                if missing:
                    line += f"  [yellow]{missing} missing[/yellow]"
                console.print(line)
                for model, cnt in sec_stats["models"].items():
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
                console.print(
                    "[bold]Most Cited External[/bold] [dim](bridging nodes)[/dim]"
                )
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

    # --- Author publications (ГОСТ Р 7.0.11-2011) ---
    author_counts = state.get_author_publication_counts()
    if author_counts:
        from .source_role import ROLE_LABELS, format_gost_phrase

        console.print()
        console.print("[bold]Публикации автора[/bold]")
        for role, cnt in sorted(author_counts.items()):
            label = ROLE_LABELS.get(role, role)
            console.print(f"  {label}: [green]{cnt}[/green]")
        gost = format_gost_phrase(author_counts)
        if gost:
            console.print(f"\n  [dim italic]{gost}[/dim italic]")

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


@main.group(invoke_without_command=True)
@click.pass_context
def gaps(ctx):
    """Reference gaps and acquisition suggestions."""
    if ctx.invoked_subcommand is None:
        console.print(
            "[yellow]Warning: `klemma gaps` is deprecated. Use `klemma status --verbose`.[/yellow]"
        )
        ctx.invoke(status, verbose=True)


main.add_command(gaps)


@main.command()
@click.option("--limit", "-n", type=int, default=10, help="Number of suggestions")
@click.option("--section", "-s", default=None, help="Filter by section (e.g. 1.3)")
@click.pass_context
def suggest(ctx, limit, section):
    """Suggest papers to fill reference gaps."""
    from .search import (
        ChainSearchProvider,
        CrossRefSearchProvider,
        S2SearchProvider,
        create_search,
    )
    from .skills.suggester import suggest_acquisitions

    kctx = _get_context(ctx)
    _sync_sections(kctx, quiet=True)

    # Fetch more gaps than needed (some won't resolve)
    gaps_list = kctx.state.get_reference_gaps(section=section, limit=limit * 3)

    if not gaps_list:
        console.print("[yellow]No open reference gaps found.[/yellow]")
        return

    # Initialize search: configured provider, or default S2 → CrossRef chain
    search = kctx.search
    if search is None:
        search_cfg = kctx.config.search
        if search_cfg.backend:
            search = create_search(search_cfg.model_dump())
        else:
            search = ChainSearchProvider(
                [
                    CrossRefSearchProvider(),
                    S2SearchProvider(),
                ]
            )

    console.print(f"\n[bold]Resolving top gaps via {search.backend_name}...[/bold]\n")

    suggest_cfg = kctx.config.suggest
    candidates, filtered_old = suggest_acquisitions(
        gaps_list,
        search,
        limit=limit,
        max_age_years=suggest_cfg.max_age_years,
        classic_min_score=suggest_cfg.classic_min_score,
    )

    if not candidates:
        msg = "[yellow]No gaps could be resolved.[/yellow]"
        if filtered_old:
            msg += f"\n[dim]{filtered_old} older papers filtered (>{suggest_cfg.max_age_years}y)[/dim]"
        console.print(msg)
        return

    total_open = len(gaps_list)
    table = Table(
        title=f"Gap Suggestions ({len(candidates)} of {total_open} open gaps)",
        show_lines=True,
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Score", justify="right", width=6)
    table.add_column("Authors", width=20)
    table.add_column("Year", width=5)
    table.add_column("Title", width=35)
    table.add_column("Sections", width=12)

    for i, c in enumerate(candidates, 1):
        year_str = str(c.ref_year) if c.ref_year else "—"
        sections_str = ", ".join(c.sections) if c.sections else "—"
        title_display = (
            c.ref_title[:60] + "..." if len(c.ref_title) > 60 else c.ref_title
        )

        table.add_row(
            str(i),
            f"{c.score:.1f}",
            c.ref_authors[:25] if c.ref_authors else "—",
            year_str,
            title_display,
            sections_str,
        )

    console.print(table)

    # Print acquire commands below the table
    console.print()
    for i, c in enumerate(candidates, 1):
        if c.acquire_cmd:
            console.print(f"  [dim]{i}.[/dim] [green]→ {c.acquire_cmd}[/green]")
        elif c.doi:
            console.print(
                f"  [dim]{i}.[/dim] [yellow]⚠ No open-access PDF found"
                f" (DOI: {c.doi})[/yellow]"
            )
        else:
            console.print(f"  [dim]{i}.[/dim] [dim]⚠ Not found in search API[/dim]")
    if filtered_old:
        console.print(
            f"  [dim]{filtered_old} older papers filtered (>{suggest_cfg.max_age_years}y, score<{suggest_cfg.classic_min_score})[/dim]"
        )
    console.print()


# Keep options in sync with top-level suggest
@gaps.command(name="suggest", hidden=True)
@click.option("--limit", "-n", type=int, default=10)
@click.option("--section", "-s", default=None)
@click.pass_context
def gaps_suggest(ctx, limit, section):
    """[alias] → suggest"""
    ctx.invoke(suggest, limit=limit, section=section)


@main.group(invoke_without_command=True)
@click.pass_context
def source(ctx):
    """Manage individual sources."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


main.add_command(source)


@source.command()
@click.argument("citekey")
@click.argument(
    "role",
    type=click.Choice(
        [
            "external",
            "author_vak",
            "author_scopus",
            "author_wos",
            "author_conf",
            "author_patent",
            "author_program",
            "author_other",
        ]
    ),
)
@click.pass_context
def role(ctx, citekey, role):
    """Assign a source_role to a source (e.g. author_vak, author_conf)."""
    kctx = _get_context(ctx)
    src = kctx.state.get_source(citekey)
    if not src:
        console.print(f"[red]Source '{citekey}' not found.[/red]")
        raise SystemExit(1)
    kctx.state.set_source_role(citekey, role)
    from .source_role import ROLE_LABELS

    label = ROLE_LABELS.get(role, role)
    console.print(f"[green]@{citekey}[/green] → {label}")


def _show_writing_order(kctx: KlemmaContext, current_section: str) -> None:
    """Display results-first writing order with current section highlighted.

    Shows section-level items within the current chapter (parsed from outline),
    falling back to chapter-level items from section_type_map DB.
    """
    import re

    from .section_types import get_writing_order, infer_section_type

    project = kctx.project
    if not project:
        return

    # Determine current chapter from section (e.g. "3.2" → 3)
    current_chapter = current_section.split(".")[0] if "." in current_section else None

    # Try to parse section-level entries from the outline file
    sections: dict[str, str] = {}
    type_map: dict[str, str] = {}

    if kctx.project_root:
        # Find outline file
        outline_pattern = re.compile(r"^Outline_.*\.md$")
        for p in sorted(kctx.project_root.iterdir()):
            if outline_pattern.match(p.name) and p.is_file():
                outline_text = p.read_text(encoding="utf-8", errors="replace")
                # Parse ### N.M. Title lines (section headings in outline)
                sec_re = re.compile(r"^###\s+(\d+\.\d+)\.?\s+(.+)", re.MULTILINE)
                for m in sec_re.finditer(outline_text):
                    sec_id, title = m.group(1), m.group(2).strip()
                    sec_chapter = sec_id.split(".")[0]
                    if current_chapter and sec_chapter == current_chapter:
                        sections[sec_id] = title
                        inferred = infer_section_type(title)
                        if inferred:
                            type_map[sec_id] = inferred.value
                break  # use first outline found

    # Fallback: chapter-level from DB if no section-level entries found
    if not sections:
        with kctx.state._conn() as conn:
            cur = conn.execute(
                "SELECT section, section_type, chapter FROM section_type_map"
            )
            for row in cur.fetchall():
                sec = row["section"]
                # Skip entries not in config chapters (stale DB rows)
                ch_num = row["chapter"]
                if project.chapters and ch_num not in project.chapters:
                    continue
                type_map[sec] = row["section_type"]
                ch_name = ""
                if ch_num and project.chapters:
                    ch_name = project.chapters.get(ch_num, "")
                sections[sec] = ch_name or row["section_type"] or sec

        if project.section_type_map:
            type_map.update(project.section_type_map)

    if not sections:
        return

    drafts_dir = (kctx.project_root / "notes" / "drafts") if kctx.project_root else None

    items = get_writing_order(sections, type_map, drafts_dir)
    if not items:
        return

    console.print("[dim]Writing order (results-first):[/dim]")
    for item in items:
        if item.section_id == current_section:
            marker = "[bold cyan]→[/bold cyan]"
            label = f"[bold cyan]{item.section_id} {item.title}[/bold cyan]"
        elif item.has_draft:
            marker = "[green]✓[/green]"
            label = f"[dim]{item.section_id} {item.title}[/dim]"
        else:
            marker = "[dim]○[/dim]"
            label = f"{item.section_id} {item.title}"
        console.print(f"  {marker} {label}")
    console.print()


@main.group(invoke_without_command=True)
@click.option(
    "--section",
    "-s",
    default=None,
    help="Section ID (e.g. 1.3.2) — standalone section draft mode",
)
@click.option("--model", default=None, help="Override AI model")
@click.option("--no-save", is_flag=True, help="Print draft without saving to file")
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
@click.pass_context
def draft(ctx, section, model, no_save, no_rag, prompt):
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

    from .config import parse_chapter_from_section
    from .skills.context_loader import (
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
    from .skills.drafter import generate_draft

    kctx = _get_context(ctx)
    cfg = kctx.config
    _sync_sections(kctx)
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

    # 8. Save to notes/drafts/
    if not no_save and kctx.project_root:
        drafts_dir = kctx.project_root / "notes" / "drafts"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        out_path = drafts_dir / f"Draft_{section}.md"
        out_path.write_text(result.text, encoding="utf-8")
        console.print(f"[green]Saved to {out_path}[/green]")
    elif no_save:
        console.print(result.text)

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
    """Generate introduction draft — 12 mandatory ГОСТ sections."""
    kctx = _get_context(ctx)
    cfg = kctx.config
    if model:
        cfg.ai.model = model
    ai = _init_ai(cfg)

    from .skills.introduction_drafter import GOST_SECTIONS, generate_introduction

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
        out_path = kctx.project_root / f"Введение{suffix}.md"

    out_path.write_text(result.text, encoding="utf-8")
    console.print(
        f"[green]Saved to {out_path}[/green] ({result.section_count} sections)"
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

    from .config import parse_chapter_from_section
    from .section_types import resolve_section_identifier
    from .skills.researcher import pre_extract_sources, research_section

    # Resolve semantic type → numeric section if possible
    resolved_section, section_type = resolve_section_identifier(section, kctx.project)
    if section_type and resolved_section:
        console.print(
            f"[dim]Resolved {section} → section {resolved_section} ({section_type.value})[/dim]"
        )
        section = resolved_section
    elif section_type and not resolved_section:
        # Fallback: check DB section_type_map (populated by sync_section_types)
        db_sections = state.get_sections_for_type(section_type.value)
        if db_sections:
            section = db_sections[0]
            console.print(
                f"[dim]Resolved {section_type.value} → section {section}[/dim]"
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

    # Пробелы
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
        console.print(
            f"\n[dim]Брифинг сохранён: notes/research/Research_{section}.md[/dim]"
        )


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

    from .config import scan_project_files
    from .skills.outliner import generate_outline as gen_outline
    from .skills.outliner import save_outline

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
                f"\n[yellow]⚠ Section structure changed "
                f"(removed: {', '.join(sorted(removed))}).[/yellow]\n"
                "[yellow]  Sources may be assigned to outdated sections.[/yellow]\n"
                "[yellow]  Run: klemma reassign[/yellow]"
            )

    # 6. Auto-generate chapter_mapping from outline chapters
    if result.chapters:
        from .config import generate_chapter_mapping, update_project_config

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

    from .skills.agent import build_agent_context, update_agents_index

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
        import subprocess as _sp

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

    from .skills.librarian import analyze_library

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
                    f"  {i}. {item.get('citekey_or_ref', '?')} — {item.get('reason', '')}"
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
            f"\n[bold yellow]Prune Analysis[/bold yellow] [dim]({total} → ~{after} sources)[/dim]"
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
                f"[bold red]── [{i}/{len(verdicts)}] @{citekey} ──[/bold red]"
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
                    f"  [dim]{abstract[:200]}{'…' if len(abstract) > 200 else ''}[/dim]"
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
    from .skills.duplicate_checker import find_duplicates

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


# --- Reassign: semantic fragment-to-section suggestion ---


@main.command()
@click.option(
    "--threshold",
    "-t",
    type=float,
    default=0.5,
    help="Minimum cosine similarity for suggestion (default: 0.5)",
)
@click.option(
    "--limit", "-n", type=int, default=20, help="Max suggestions to show (default: 20)"
)
@click.option(
    "--apply",
    is_flag=True,
    default=False,
    help="Apply suggestions: add sections to vault frontmatter",
)
@click.option(
    "--fresh",
    is_flag=True,
    default=False,
    help="Clear saved skip decisions and show all suggestions",
)
@click.pass_context
def reassign(ctx, threshold, limit, apply, fresh):
    """Suggest fragment-to-section reassignments based on embedding similarity."""
    from .embeddings import cosine_similarity

    kctx = _get_context(ctx)
    state = kctx.state
    _sync_sections(kctx)

    # 1. Load section embeddings
    section_embeddings = state.get_all_section_embeddings()
    if not section_embeddings:
        console.print(
            "[red]No section embeddings found. "
            "Run 'klemma embed sections' first.[/red]"
        )
        raise SystemExit(1)

    # 2. Load fragment embeddings + metadata
    frag_embeddings = state.get_fragment_embeddings()
    if not frag_embeddings:
        console.print(
            "[red]No fragment embeddings found. "
            "Run 'klemma embed fragments' first.[/red]"
        )
        raise SystemExit(1)

    frag_meta = state.get_embedded_fragment_metadata()
    meta_by_id = {m["id"]: m for m in frag_meta}

    # Filter to active sources only (exclude orphaned fragments)
    active_sources = state.get_existing_source_ids()

    # Build section name lookup from project config (chapter-level names)
    chapter_names: dict[str, str] = {}
    project = kctx.project
    if project:
        for num, title in (project.chapters or {}).items():
            chapter_names[str(num)] = title

    def _section_label(sec_id: str) -> str:
        """Resolve section ID to chapter name. '3.3' → 'Гл. 3: <title>'."""
        if sec_id in chapter_names:
            return chapter_names[sec_id]
        chapter_num = sec_id.split(".")[0]
        name = chapter_names.get(chapter_num)
        if name:
            return f"Гл. {chapter_num}"
        return ""

    # 3. Compute best section match for each fragment
    raw_suggestions = []

    with console.status(
        f"Computing affinity for {len(frag_embeddings)} fragments "
        f"× {len(section_embeddings)} sections...",
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

            ranked = sorted(scores.items(), key=lambda x: -x[1])
            best_section, best_score = ranked[0]
            current_score = scores.get(current_section, 0.0)

            # Only suggest if different from current and above threshold
            if (
                best_section
                and best_section != current_section
                and best_score >= threshold
            ):
                raw_suggestions.append(
                    {
                        "frag_id": frag_id,
                        "citekey": source_id,
                        "current": current_section or "(none)",
                        "suggested": best_section,
                        "score": best_score,
                        "current_score": current_score,
                        "delta": best_score - current_score,
                        "runner_up": ranked[1] if len(ranked) > 1 else None,
                        "preview": (meta.get("text_preview") or "")[:80],
                    }
                )

    # Group by (citekey, current, suggested) — collect all fragment IDs per group
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

    # Filter out previously skipped suggestions
    if fresh:
        cleared = state.clear_reassign_skips()
        if cleared:
            console.print(f"[dim]Cleared {cleared} saved skip(s).[/dim]")
        skips: set[tuple[str, str, str]] = set()
    else:
        skips = state.get_reassign_skips()

    suggestions = []
    skipped_by_memory = 0
    for key, group in groups.items():
        source_id, from_sec, to_sec = key
        if (source_id, from_sec, to_sec) in skips:
            skipped_by_memory += 1
            continue
        suggestions.append(group)

    if skipped_by_memory:
        console.print(
            f"[dim]Filtered {skipped_by_memory} previously skipped suggestion(s). "
            f"Use --fresh to reset.[/dim]"
        )

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

        console.print(f"[bold]── [{i}/{len(suggestions)}] @{s['citekey']} ──[/bold]")
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
        console.print()

    if not apply:
        console.print(
            f"[dim]{total} suggestions total (showing top {len(suggestions)}). "
            f"Use --apply to reassign fragments interactively.[/dim]"
        )
        return

    # --- Apply: per-item interactive confirmation (ADR-011) ---
    from .cli_confirm import ReviewItem, interactive_review

    vault = kctx.vault
    notes_folder = kctx.config.obsidian.notes_folder

    # Build section description: type + sample source titles
    section_type_labels: dict[str, str] = {}
    if project:
        type_map = project.section_type_map or {}
        for sec_id, sec_type in type_map.items():
            section_type_labels[sec_id] = sec_type
        # Infer subsection types from parent: 1.4.1 → type of "1"
        for ch_num in project.chapters or {}:
            if str(ch_num) in type_map:
                section_type_labels[str(ch_num)] = type_map[str(ch_num)]

    # Sample source titles per section (top 3 by relevance)
    section_samples: dict[str, list[str]] = {}
    try:
        with state._conn() as conn:
            cur = conn.execute(
                """SELECT ss.section, s.title
                   FROM source_sections ss
                   JOIN sources s ON ss.source_id = s.id
                   WHERE s.title IS NOT NULL AND s.title != ''
                   ORDER BY ss.section"""
            )
            for row in cur.fetchall():
                sec = row["section"]
                section_samples.setdefault(sec, [])
                if len(section_samples[sec]) < 3:
                    title = row["title"]
                    if title and len(title) > 60:
                        title = title[:57] + "..."
                    if title:
                        section_samples[sec].append(title)
    except Exception:
        pass

    def _describe_section(sec_id: str) -> str:
        parts = []
        # Section type (inherit from parent chapter if not explicitly mapped)
        st = section_type_labels.get(sec_id)
        if not st:
            ch = sec_id.split(".")[0]
            st = section_type_labels.get(ch)
        if st:
            parts.append(f"[{st}]")
        # Chapter name (short — just the chapter number)
        ch_num = sec_id.split(".")[0]
        ch_name = chapter_names.get(ch_num)
        if ch_name and sec_id != ch_num:
            # Truncate long chapter names for subsections
            short = ch_name[:50] + "..." if len(ch_name) > 50 else ch_name
            parts.append(f"Гл. {ch_num}: {short}")
        elif ch_name:
            parts.append(ch_name)
        # Sample sources in this section
        samples = section_samples.get(sec_id, [])
        if samples:
            parts.append(f"({len(samples)}+ sources: {samples[0]})")
        return " | ".join(parts) if parts else sec_id

    # Load full fragment texts for detailed display
    frag_texts: dict[int, str] = {}
    all_frag_ids: list[int] = []
    for s in suggestions:
        all_frag_ids.extend(s.get("frag_ids", [s["frag_id"]]))
    if all_frag_ids:
        with state._conn() as conn:
            placeholders = ",".join("?" * len(all_frag_ids))
            cur = conn.execute(
                f"SELECT id, fragment_text FROM fragments WHERE id IN ({placeholders})",
                all_frag_ids,
            )
            frag_texts = {row["id"]: row["fragment_text"] for row in cur.fetchall()}

    # Build ReviewItems
    review_items = []
    for s in suggestions:
        frag_ids_list = s.get("frag_ids", [s["frag_id"]])
        frag_text = frag_texts.get(s["frag_id"], s["preview"])
        cur_sec = s["current"]
        sug_sec = s["suggested"]
        cur_desc = _describe_section(cur_sec) if cur_sec != "(none)" else "unassigned"
        sug_desc = _describe_section(sug_sec)
        score_style = "green" if s["score"] >= 0.7 else "yellow"
        frag_count = len(frag_ids_list)
        count_note = f" ({frag_count} fragments)" if frag_count > 1 else ""

        review_items.append(
            ReviewItem(
                key=f"{s['citekey']}:{cur_sec}:{sug_sec}",
                header=f"@{s['citekey']}{count_note}",
                details=[
                    ("Fragment", frag_text),
                    (
                        "Current",
                        f"{cur_sec} — {cur_desc}  (sim={s['current_score']:.3f})",
                    ),
                    (
                        "Suggested",
                        f"[{score_style}]{sug_sec}[/{score_style}] — "
                        f"{sug_desc}  (sim={s['score']:.3f}, +{s['delta']:.3f})",
                    ),
                ],
                action_label=(
                    f"Move {frag_count} fragment(s) from {cur_sec} → {sug_sec}"
                ),
                data={
                    "citekey": s["citekey"],
                    "frag_ids": frag_ids_list,
                    "section": sug_sec,
                    "from_section": cur_sec,
                },
            )
        )

    result = interactive_review(
        review_items,
        console=console,
        title="Review reassignment suggestions",
    )

    # Save skip decisions for items not accepted
    new_skips: list[tuple[str, str, str]] = []
    accepted_keys = {item.key for item in result.accepted}
    for item in review_items:
        if item.key not in accepted_keys:
            new_skips.append(
                (
                    item.data["citekey"],
                    item.data["from_section"],
                    item.data["section"],
                )
            )
    if new_skips:
        state.save_reassign_skips_batch(new_skips)

    if not result.accepted:
        console.print(f"[dim]No changes applied ({result.skipped} skipped).[/dim]")
        return

    # 1. Update fragment sections in DB — move ALL fragments per group
    moved = 0
    for item in result.accepted:
        for frag_id in item.data["frag_ids"]:
            ok = state.update_fragment_section(frag_id, item.data["section"])
            if ok:
                moved += 1

    # 2. Also add new sections to vault frontmatter (if source not already associated)
    vault_updates = 0
    additions: dict[str, set[str]] = {}
    for item in result.accepted:
        additions.setdefault(item.data["citekey"], set()).add(item.data["section"])

    for citekey, new_sections in additions.items():
        note_name = f"@{citekey}"
        props = vault.get_properties(note_name)
        if not props:
            continue

        current = set(str(s) for s in props.get("sections", []))
        merged = current | new_sections
        if merged == current:
            continue

        ok = vault.update_frontmatter_sections(
            note_name,
            list(merged),
            folder=notes_folder,
        )
        if ok:
            added = sorted(merged - current)
            vault_updates += 1
            console.print(
                f"  [dim]Vault @{citekey}: added sections {', '.join(added)}[/dim]"
            )

    console.print(
        f"\n[green]{moved} fragment(s) reassigned in DB, "
        f"{result.skipped} skipped.[/green]"
        + (
            f" [dim]{vault_updates} vault note(s) updated.[/dim]"
            if vault_updates
            else ""
        )
    )


# --- Add: unified source ingestion ---


def _detect_input_type(value: str) -> str:
    """Detect whether input is a URL, local PDF path, or citekey.

    Returns 'url', 'path', or 'citekey'.
    """
    if value.startswith(("http://", "https://", "doi:")):
        return "url"
    p = Path(value)
    if p.suffix.lower() == ".pdf" and p.exists():
        return "path"
    return "citekey"


@main.command()
@click.argument("input_value")
@click.option("--section", "-s", multiple=True, help="Dissertation section(s) to assign")
@click.option("--title", "-t", help="Paper title (URL mode only)")
@click.option("--authors", "-a", help="Authors, comma-separated (URL mode only)")
@click.option("--year", "-y", type=int, help="Publication year (URL mode only)")
@click.option(
    "--no-process", is_flag=True, help="Skip fragment extraction"
)
@click.option(
    "--no-embed", is_flag=True, help="Skip auto-embedding after processing"
)
@click.option(
    "--model", default=None, help="Override AI model (e.g. openai/gpt-4.1-mini)"
)
@click.pass_context
def add(ctx, input_value, section, title, authors, year, no_process, no_embed, model):
    """Add a paper: URL, citekey, or local PDF path.

    Auto-detects input type and runs the full pipeline:

      klemma add <url>       --section 1.1
      klemma add <citekey>   --section 2.3
      klemma add <paper.pdf> --section 1.2
    """
    from .skills.acquirer import PaperMetadata, acquire_paper_local

    kctx = _get_context(ctx)
    cfg, state = kctx.config, kctx.state
    if model:
        cfg.ai.model = model
    sections = list(section)
    input_type = _detect_input_type(input_value)

    citekey = None
    total_frags = 0

    if input_type == "url":
        # --- URL mode: acquire → process → embed ---
        meta = PaperMetadata(
            url=input_value,
            title=title or "",
            authors=authors or "",
            year=year,
            sections=sections,
        )
        console.print(f"[bold]Adding from URL:[/bold] {input_value[:80]}")
        result = acquire_paper_local(
            meta,
            storage_path=cfg.zotero.storage_path,
            state=state,
        )
        if result.status not in ("ok", "ok_no_pdf"):
            console.print(f"  [red]{result.status}[/red]")
            return
        citekey = result.citekey
        console.print(f"  [green]@{citekey}[/green]")
        if result.zotero_added:
            console.print("  [blue]Added to Zotero[/blue]")
        if result.status == "ok_no_pdf":
            console.print("  [yellow]No open-access PDF (metadata-only)[/yellow]")
            no_process = True  # can't process without PDF

    elif input_type == "path":
        # --- Local PDF mode: copy + register → process → embed ---
        pdf_path = Path(input_value).resolve()
        console.print(f"[bold]Adding from PDF:[/bold] {pdf_path.name}")
        # Use acquire with file:// URL — acquirer handles local paths
        meta = PaperMetadata(
            url=f"file://{pdf_path}",
            title=title or "",
            authors=authors or "",
            year=year,
            sections=sections,
            pdf_override=str(pdf_path),
        )
        result = acquire_paper_local(
            meta,
            storage_path=cfg.zotero.storage_path,
            state=state,
        )
        if result.status not in ("ok", "ok_no_pdf"):
            console.print(f"  [red]{result.status}[/red]")
            return
        citekey = result.citekey
        console.print(f"  [green]@{citekey}[/green]")

    elif input_type == "citekey":
        # --- Citekey mode: assign sections + process if needed ---
        citekey = input_value
        source = state.get_source(citekey)
        if not source:
            console.print(f"[red]Source @{citekey} not found in database.[/red]")
            console.print("[dim]Use a URL or PDF path to add a new source.[/dim]")
            return
        console.print(f"[bold]Adding sections to:[/bold] @{citekey}")

    # --- Section assignment (all input types) ---
    if sections and citekey:
        chapters = list({int(s.split(".")[0]) for s in sections if "." in s})
        state.set_source_sections(citekey, sections, chapters)
        # Update vault note frontmatter if note exists
        if kctx.vault:
            note_name = f"@{citekey}"
            notes_folder = kctx.config.obsidian.notes_folder or None
            kctx.vault.update_frontmatter_sections(note_name, sections, folder=notes_folder)
        console.print(f"  [dim]sections: {', '.join(sections)}[/dim]")

    # --- Process (if not suppressed and we have a citekey) ---
    if citekey and not no_process:
        source = state.get_source(citekey)
        status = source["status"] if source else None

        # Process if: pending/failed, or citekey mode (force reprocess).
        # Don't check pdf_path here — _process_single() has its own PDF
        # discovery via pdf_extractor.find_pdf() which searches Zotero storage.
        should_process = status in ("pending", "failed", None) or (
            input_type == "citekey" and status == "completed"
        )
        if should_process:
            try:
                ai = _init_ai(cfg)
            except Exception as e:
                console.print(f"  [yellow]Skipping process (AI unavailable: {e})[/yellow]")
                ai = None

            if ai:
                from .literature.pdf import PDFExtractor

                pdf_extractor = PDFExtractor(max_chars=cfg.ai.max_pdf_chars)
                force = input_type == "citekey" and status == "completed"
                with console.status(
                    f"Extracting fragments from @{citekey}", spinner="arc"
                ):
                    n_frags, _ = _process_single(
                        citekey,
                        cfg,
                        state,
                        kctx.vault,
                        ai,
                        pdf_extractor,
                        kctx.library,
                        dissertation_context=kctx.dissertation_context,
                        available_tags=kctx.available_tags,
                        klemma_home=kctx.klemma_home,
                        project_type=(
                            kctx.project.type if kctx.project else "dissertation"
                        ),
                        embeddings=kctx.embeddings,
                        no_embed=no_embed,
                        force=force,
                    )
                total_frags = n_frags

    # --- Summary ---
    parts = [f"@{citekey}"]
    if total_frags:
        parts.append(f"{total_frags} fragments")
    if sections:
        parts.append(f"sections {', '.join(sections)}")
    console.print(f"\n[green]Done: {', '.join(parts)}.[/green]")


# --- Acquire: download + add to Zotero + register ---


@main.command()
@click.argument("url", required=False)
@click.option("--title", "-t", help="Paper title")
@click.option("--authors", "-a", help="Authors (comma-separated)")
@click.option("--year", "-y", type=int, help="Publication year")
@click.option("--journal", "-j", help="Journal name")
@click.option("--volume", help="Volume")
@click.option("--issue", help="Issue")
@click.option(
    "--section", "-s", multiple=True, help="Dissertation section(s) to assign"
)
@click.option(
    "--pdf",
    "pdf_url",
    help="Direct PDF URL (bypass DOI resolution, e.g. for WAF-protected publishers)",
)
@click.option(
    "--batch",
    "batch_path",
    type=click.Path(exists=True),
    help="JSON file with papers list",
)
@click.option(
    "--no-process", is_flag=True, help="Skip fragment extraction after adding"
)
@click.option(
    "--no-embed", is_flag=True, help="Skip auto-embedding after processing"
)
@click.pass_context
def acquire(
    ctx,
    url,
    title,
    authors,
    year,
    journal,
    volume,
    issue,
    section,
    pdf_url,
    batch_path,
    no_process,
    no_embed,
):
    """Download PDF, add to Zotero, register in klemma.

    Single paper: klemma acquire <pdf_url> --title "..." --authors "..." --year 2022 --section 1.2
    With DOI + direct PDF: klemma acquire <doi_url> --pdf <direct_pdf_url> --section 1.3
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
        papers = [
            PaperMetadata(
                url=url,
                title=title or "",
                authors=authors or "",
                year=year,
                journal=journal or "",
                volume=volume or "",
                issue=issue or "",
                pdf_override=pdf_url or "",
                sections=list(section),
            )
        ]
    else:
        console.print("[red]Provide a URL or --batch file[/red]")
        return

    ok = 0
    total_frags = 0

    for i, meta in enumerate(papers, 1):
        label = meta.title[:50] if meta.title else meta.url[:50]
        console.print(f"\n[bold][{i}/{len(papers)}] {label}[/bold]")

        result = acquire_paper_local(
            meta,
            storage_path=cfg.zotero.storage_path,
            state=state,
        )

        if result.status in ("ok", "ok_no_pdf"):
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
            if result.status == "ok_no_pdf":
                console.print(
                    "  [yellow]No open-access PDF found (metadata-only)[/yellow]"
                )
                if result.zotero_added:
                    console.print(
                        "  [dim]Tip: use Zotero → Right-click → Find Available PDF[/dim]"
                    )
            if meta.sections:
                console.print(f"  [dim]sections: {', '.join(meta.sections)}[/dim]")

            if not no_process and result.status == "ok":
                try:
                    ai = _init_ai(cfg)
                except Exception as e:
                    console.print(
                        f"  [yellow]Skipping auto-process (AI unavailable: {e})[/yellow]"
                    )
                    console.print(
                        f"  [dim]Run manually: klemma process {result.citekey}[/dim]"
                    )
                    ai = None

                if ai:
                    from .literature.pdf import PDFExtractor

                    pdf_extractor = PDFExtractor(max_chars=cfg.ai.max_pdf_chars)
                    with console.status(
                        f"Extracting fragments from @{result.citekey}", spinner="arc"
                    ):
                        n_frags, _ = _process_single(
                            result.citekey,
                            cfg,
                            state,
                            kctx.vault,
                            ai,
                            pdf_extractor,
                            kctx.library,
                            dissertation_context=kctx.dissertation_context,
                            available_tags=kctx.available_tags,
                            klemma_home=kctx.klemma_home,
                            project_type=(
                                kctx.project.type if kctx.project else "dissertation"
                            ),
                            embeddings=kctx.embeddings,
                            no_embed=no_embed,
                        )
                        total_frags += n_frags

            ok += 1
        else:
            console.print(f"  [red]{result.status}[/red]")

    parts = [f"{ok}/{len(papers)} acquired"]
    if total_frags:
        parts.append(f"{total_frags} fragments")
    console.print(f"\n[green]Done: {', '.join(parts)}.[/green]")

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
        nf_s = "[green]✓[/green]" if nf_ok else "[red]✗ not found[/red]"
        tf_s = "[green]✓[/green]" if tf_ok else "[red]✗ not found[/red]"
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
            if (
                child.is_dir()
                and (child / ".klemma").is_dir()
                and child.name != ".klemma"
            ):
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
        non_default = {
            k: v for k, v in params.items() if v is not None and k != "prompt_variant"
        }
        if non_default or params.get("prompt_variant") != "default":
            console.print(f"[dim]Ablation: {params}[/dim]")

    with console.status("Running autonomous benchmark pipeline...", spinner="arc"):
        result = run_auto_benchmark(
            kctx.state,
            ai,
            kctx.config,
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
        kctx.state,
        citekey,
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
        pdf_str = (
            "[green]yes[/green]"
            if ref.resolved and ref.resolved.pdf_url
            else "[red]no[/red]"
        )
        status_color = {
            "in_library": "green",
            "resolved": "blue",
            "no_pdf": "yellow",
            "failed": "red",
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
        kctx.state,
        citekey,
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
        kctx.state,
        ai,
        citekey,
        kctx.config,
        kctx.klemma_home,
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
        console.print(
            "Save as JSON and add to your benchmark dataset under 'reconstruction' key."
        )
        click.echo(json.dumps(dataset.model_dump(), indent=2))


def _print_reconstruction_results(recon: dict):
    """Print reconstruction benchmark results as Rich panels."""
    # Ground truth summary
    gt = recon.get("ground_truth", {})
    console.print(
        Panel(
            f"Paper: {gt.get('paper', 'N/A')}\n"
            f"Sections: {gt.get('sections', 0)}, "
            f"Bibliography: {gt.get('bibliography_size', 0)}, "
            f"In-library samples: {gt.get('samples', 0)}",
            title="Reconstruction: Ground Truth",
        )
    )

    # Baseline results (source-coverage)
    bl = recon.get("baseline", {})
    if bl:
        console.print(
            Panel(
                f"Source coverage: {bl.get('sources_covered', 0)}/{bl.get('sources_total', 0)} "
                f"({bl.get('source_coverage', 0):.1%})\n"
                f"Intent coverage: {bl.get('intent_coverage', 0):.1%}",
                title="Reconstruction: Baseline (library coverage)",
            )
        )

    # AI reconstruction results
    rc = recon.get("reconstruction", {})
    if rc:
        if rc.get("error"):
            console.print(f"[yellow]Reconstruction: {rc['error']}[/yellow]")
        else:
            console.print(
                Panel(
                    f"Predictions: {rc.get('predictions_count', 0)}\n"
                    f"Macro-P: {rc.get('macro_precision', 0):.4f}  "
                    f"Macro-R: {rc.get('macro_recall', 0):.4f}  "
                    f"[bold]F1: {rc.get('f1', 0):.4f}[/bold]\n"
                    f"Intent accuracy: {rc.get('intent_accuracy', 0):.4f}  "
                    f"nDCG avg: {rc.get('ndcg_avg', 0):.4f}",
                    title="Reconstruction: AI-driven",
                )
            )

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
@click.option(
    "--dataset",
    "-d",
    type=click.Path(exists=True),
    help="Path to annotated benchmark dataset JSON",
)
@click.option(
    "--metrics",
    "-m",
    type=click.Choice(["all", "intent", "gaps", "embeddings", "reconstruct"]),
    default="all",
    help="Which benchmarks to run (default: all)",
)
@click.option(
    "--export",
    "export_path",
    type=click.Path(),
    help="Export current DB data as dataset template for annotation",
)
@click.option(
    "--json-output", is_flag=True, help="Output results as JSON for reproducibility"
)
@click.option(
    "--semantic",
    is_flag=True,
    help="Apply semantic reranking to gap benchmark (hybrid keyword × semantic mode)",
)
@click.option(
    "--analyst",
    "analyst_citekey",
    type=str,
    default=None,
    help="Run analyst prompt on a paper PDF to extract ground truth citation map",
)
@click.option(
    "--reconstruct",
    "reconstruct",
    is_flag=True,
    help="Run citation reconstruction benchmark (requires reconstruction field in dataset)",
)
@click.option("--history", is_flag=True, help="Show past benchmark run history")
@click.option(
    "--compare",
    nargs=2,
    type=str,
    default=None,
    help="Compare two runs: --compare <id1> <id2>",
)
@click.option(
    "--export-history",
    "export_history_path",
    type=click.Path(),
    help="Export benchmark run history as JSON for archival",
)
@click.option(
    "--candidates",
    is_flag=True,
    help="Show benchmark candidate papers ranked by citation graph coverage",
)
@click.option(
    "-k",
    "candidates_limit",
    type=int,
    default=10,
    help="Number of candidates to show (default: 10)",
)
@click.option(
    "--prepare",
    "prepare_citekey",
    type=str,
    default=None,
    help="Fetch missing referenced papers for a citekey (dry-run first)",
)
@click.option(
    "--auto",
    "auto_mode",
    is_flag=True,
    help="Run full autonomous pipeline: select → prepare → analyst → benchmark → persist",
)
@click.option(
    "--paper",
    "auto_paper",
    type=str,
    default=None,
    help="Citekey for --auto mode (default: top candidate)",
)
@click.option(
    "--skip-prepare", is_flag=True, help="Skip reference preparation in --auto mode"
)
@click.option(
    "--temperature",
    "ablation_temperature",
    type=float,
    default=None,
    help="Override AI temperature for ablation (default: 0.2)",
)
@click.option(
    "--max-recs",
    "ablation_max_recs",
    type=int,
    default=None,
    help="Max recommendations per section (default: uncapped)",
)
@click.option(
    "--fragments",
    "ablation_fragments",
    type=int,
    default=None,
    help="Fragments per source for context (default: 5)",
)
@click.option(
    "--prompt-variant",
    "ablation_variant",
    type=click.Choice(["default", "fewshot"]),
    default=None,
    help="Prompt variant for ablation (default: default)",
)
@click.pass_context
def benchmark(
    ctx,
    dataset,
    metrics,
    export_path,
    json_output,
    semantic,
    analyst_citekey,
    reconstruct,
    history,
    compare,
    export_history_path,
    candidates,
    candidates_limit,
    prepare_citekey,
    auto_mode,
    auto_paper,
    skip_prepare,
    ablation_temperature,
    ablation_max_recs,
    ablation_fragments,
    ablation_variant,
):
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
    _sync_sections(kctx, quiet=True)

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
        console.print(
            f"[green]Exported {len(runs)} runs to {export_history_path}[/green]"
        )
        return

    # --- Candidates mode ---
    if candidates:
        from .evaluation.candidates import discover_candidates

        cands = discover_candidates(kctx.state, limit=candidates_limit)
        if not cands:
            console.print(
                "[yellow]No benchmark candidates found (need sources with ≥3 in-library citations)[/yellow]"
            )
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
        if any(
            v is not None
            for v in [
                ablation_temperature,
                ablation_max_recs,
                ablation_fragments,
                ablation_variant,
            ]
        ):
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
        console.print(f"[green]Exported {count} items to {export_path}[/green]")
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
    recon_info = (
        f", reconstruction: {len(ds.reconstruction.samples)} samples"
        if ds.reconstruction
        else ""
    )
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
        console.print(
            "[yellow]--semantic requires embeddings to be configured[/yellow]"
        )

    # Determine effective metrics filter
    effective_metrics = "reconstruct" if reconstruct else metrics

    # Build ablation params for -d mode (same logic as --auto)
    from .evaluation.pipeline import AblationParams, compute_prompt_hash

    ablation = None
    if any(
        v is not None
        for v in [
            ablation_temperature,
            ablation_max_recs,
            ablation_fragments,
            ablation_variant,
        ]
    ):
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
        non_default = {
            k: v for k, v in params.items() if v is not None and k != "prompt_variant"
        }
        if non_default or params.get("prompt_variant") != "default":
            console.print(f"[dim]Ablation: {params}[/dim]")

    # Initialize AI if reconstruction benchmark is requested
    ai = None
    if (effective_metrics in ("all", "reconstruct")) and ds.reconstruction:
        try:
            ai = _init_ai(kctx.config)
        except Exception:
            console.print(
                "[dim]AI not available — reconstruction will run baseline only[/dim]"
            )

    results = run_all(
        kctx.state,
        ds,
        effective_metrics,
        reranked_gaps=reranked_gaps,
        ai=ai,
        klemma_home=kctx.klemma_home,
        ablation=ablation,
    )

    duration = time.monotonic() - t_start

    # --- Persist run ---
    ds_hash = compute_dataset_hash(dataset)
    git_commit = ""
    try:
        git_commit = (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            .decode()
            .strip()
        )
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
        console.print(
            Panel(
                f"Matched: {ir['matched']}/{ir['total']} "
                f"(skipped: {ir.get('skipped', 0)})\n"
                f"[bold]Macro-F1: {m.get('macro_f1', 0):.4f}[/bold]  "
                f"Accuracy: {m.get('accuracy', 0):.4f}",
                title="Intent Classification",
            )
        )
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
        gap_title = (
            "Gap Ranking [dim](hybrid: keyword × semantic)[/dim]"
            if semantic
            else "Gap Ranking"
        )
        console.print(
            Panel(
                f"Ground truth: {gr['total']} gaps, "
                f"DB gaps: {gr.get('db_gaps_count', 0)}\n"
                f"Precision@5: {gm.get('precision_at_5', 0):.4f}  "
                f"Precision@10: {gm.get('precision_at_10', 0):.4f}  "
                f"[bold]nDCG@10: {gm.get('ndcg_at_10', 0):.4f}[/bold]",
                title=gap_title,
            )
        )

    if "embeddings" in results:
        er = results["embeddings"]
        em = er.get("metrics", {})
        if er.get("error"):
            console.print(f"[yellow]Embeddings: {er['error']}[/yellow]")
        else:
            console.print(
                Panel(
                    f"Queries: {er.get('evaluated', 0)}/{er['total_queries']} "
                    f"(skipped: {er.get('skipped', 0)})\n"
                    f"Recall@5: {em.get('avg_recall_at_5', 0):.4f}  "
                    f"[bold]Recall@10: {em.get('avg_recall_at_10', 0):.4f}[/bold]  "
                    f"Precision@5: {em.get('avg_precision_at_5', 0):.4f}",
                    title="Embedding Retrieval",
                )
            )

    if "reconstruction" in results:
        _print_reconstruction_results(results["reconstruction"])


# --- Migrate: convert old ~/.klemma/ to per-directory project ---


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
    from .setup import migrate_content_to_klemma_md

    kctx = _get_context(ctx)
    project_root = kctx.project_root

    if dry_run:
        console.print("[bold]Dry run — showing what would be migrated:[/bold]\n")
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
