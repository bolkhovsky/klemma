"""Klemma CLI — AI academic assistant."""

import logging
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__, get_banner
from .ai import create_ai
from .config import (
    discover_project_chain,
    discover_project_root,  # noqa: F401 — imported for test mocking via klemma.cli.discover_project_root
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
logger = logging.getLogger(__name__)

# CLI command → task name for model routing (used in status line)
_CMD_TASK_MAP = {
    "plan": "planner",
    "process": "extract",
    "research": "research",
    "library": "library_status",
    "ask": "ask",
    "outline": "outline_initial",
}


def _auto_migrate_to_three_tier(klemma_home: Path, lib_db: Path) -> tuple[int, int, int]:
    """Migrate monolithic klemma.db → library.db + project.db non-destructively.

    Called automatically from _init_components() when project.db is empty but
    klemma.db has sources. Creates a .db.bak backup before writing.

    Returns (n_papers, n_fragments, n_sections) or (0, 0, 0) if nothing to migrate.
    """
    import shutil
    import sqlite3

    mono_db = klemma_home / "data" / "klemma.db"
    if not mono_db.exists():
        return 0, 0, 0

    conn = sqlite3.connect(str(mono_db))
    conn.row_factory = sqlite3.Row

    sources = conn.execute(
        "SELECT id, title, authors, year, abstract, doi, status, pdf_path, quality_score"
        " FROM sources"
    ).fetchall()
    if not sources:
        conn.close()
        return 0, 0, 0

    fragments = conn.execute(
        "SELECT source_id, fragment_text, fragment_type, page_number, citation_intent"
        " FROM fragments"
    ).fetchall()

    has_sections_tbl = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='source_sections'"
    ).fetchone() is not None
    sections = (
        conn.execute("SELECT source_id, section FROM source_sections").fetchall()
        if has_sections_tbl else []
    )
    conn.close()

    # Backup before writing anything — skip if backup already exists from prior run
    bak = mono_db.with_suffix(".db.bak")
    if not bak.exists():
        shutil.copy2(mono_db, bak)

    from .hashing import compute_content_hash, compute_prompt_hash
    from .models import FragmentRecord
    from .stores import LocalPaperStore, LocalProjectStore, LocalUserLibrary

    paper_store = LocalPaperStore(lib_db)
    user_lib = LocalUserLibrary(lib_db)

    frag_by_citekey: dict[str, list] = {}
    for f in fragments:
        frag_by_citekey.setdefault(f["source_id"], []).append(f)

    citekey_to_paper_id: dict[str, str] = {}
    migrated_frags = 0

    for src in sources:
        citekey = src["id"]
        pdf_hash = f"migrated:{citekey}"
        paper_id = paper_store.register_paper(
            title=src["title"] or citekey,
            authors=src["authors"] or "",
            year=src["year"],
            doi=src["doi"] or None,
            abstract=src["abstract"] or "",
            pdf_hash=pdf_hash,
        )
        citekey_to_paper_id[citekey] = paper_id
        user_lib.add_source(
            paper_id, citekey,
            status=src["status"] or "pending",
            pdf_path=src["pdf_path"],
            quality_score=src["quality_score"],
        )
        ck_frags = frag_by_citekey.get(citekey, [])
        if ck_frags:
            p_hash = compute_prompt_hash("migrated")
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
            migrated_frags += paper_store.save_fragments(paper_id, records, p_hash, "migrated")

    proj_store = LocalProjectStore(klemma_home / "data" / "project.db")
    secs_by_citekey: dict[str, list[str]] = {}
    for s in sections:
        secs_by_citekey.setdefault(s["source_id"], []).append(s["section"])

    def _section_chapter(sec: str) -> int | None:
        """Infer chapter number from section string (e.g. '1.1' → 1)."""
        try:
            return int(sec.split(".")[0])
        except (ValueError, IndexError, AttributeError):
            return None

    for citekey, paper_id in citekey_to_paper_id.items():
        sec_list = secs_by_citekey.get(citekey, [])
        chap_list = [c for c in (_section_chapter(s) for s in sec_list) if c is not None]
        # Always register in project_sources (even with no sections) so
        # count_sources() > 0 after migration and auto-migrate doesn't re-trigger
        proj_store.set_source_sections(citekey, paper_id, sec_list, chap_list)

    n_sections = sum(len(v) for v in secs_by_citekey.values())
    return len(sources), migrated_frags, n_sections


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

    # Three-tier library (ADR-014 Phase 1B/1C): shared stores at ~/.klemma/library.db
    from .stores import LocalPaperStore, LocalProjectStore, LocalUserLibrary

    if cfg.library_db_path:
        _p = cfg.library_db_path.expanduser()
        _lib_db = _p if _p.is_absolute() else (system_home / _p)
    else:
        _lib_db = system_home / "library.db"
    paper_store = LocalPaperStore(_lib_db)
    user_library = LocalUserLibrary(_lib_db)
    project_store = LocalProjectStore(klemma_home / "data" / "project.db")

    # Auto-migration: monolithic klemma.db has data but three-tier stores are empty
    if project_store.count_sources() == 0 and state.get_stats().get("total", 0) > 0:
        console.print(
            "[cyan]Auto-migrating to three-tier layout (library.db + project.db)...[/cyan]",
            end=" ",
        )
        try:
            n_src, n_frag, n_sec = _auto_migrate_to_three_tier(klemma_home, _lib_db)
            if n_src > 0:
                console.print(
                    f"[green]done.[/green] {n_src} sources, {n_frag} fragments, "
                    f"{n_sec} section entries. Backup: {klemma_home}/data/klemma.db.bak"
                )
            else:
                console.print("[dim]nothing to migrate.[/dim]")
        except Exception as exc:  # noqa: BLE001
            console.print(
                f"[yellow]Auto-migration failed ({exc}). "
                "Run 'klemma migrate-library --apply' manually.[/yellow]"
            )

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
        paper_store=paper_store,
        user_library=user_library,
        project_store=project_store,
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

        # Phase 1 fallback (issue #105): singular `section:` treated as [section]
        # when plural `sections:` list is absent or empty — backward compat.
        primary_section_str = str(props.get("section", "")) or None
        if not sections_list and primary_section_str:
            sections_list = [primary_section_str]

        # Warn on bare chapter assignments (e.g. sections: [1] instead of 1.1)
        for _sec in sections_list:
            if str(_sec).isdigit():
                console.print(
                    f"[yellow]Warning:[/yellow] @{citekey} assigned to bare chapter "
                    f"'{_sec}' — did you mean a subsection (e.g. {_sec}.1)?"
                )

        vault_data.append(
            {
                "citekey": citekey,
                "primary_section": primary_section_str,
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
            # Track citekeys claimed by a rename in this run to prevent
            # a second orphan from resolving to the same target and being
            # silently deleted (data-loss bug #230).
            claimed_new_keys: set[str] = set()
            for old_ck in list(orphans):
                result = resolve_orphan(old_ck, bbt_index)
                if not result:
                    continue
                new_ck, item_key = result
                if new_ck in claimed_new_keys:
                    # Another orphan already claimed this target in this run —
                    # skip rather than delete (verbose-mutations: must be visible).
                    if not quiet:
                        console.print(
                            f"  [yellow]Skip:[/yellow] @{old_ck} → @{new_ck}"
                            " (collision with earlier rename, left unchanged)"
                        )
                    continue
                if new_ck in existing:
                    state.delete_source(old_ck)
                    existing.discard(old_ck)
                    renames.append((old_ck, new_ck))
                else:
                    state.rename_source(old_ck, new_ck, item_key)
                    existing.discard(old_ck)
                    existing.add(new_ck)
                    claimed_new_keys.add(new_ck)
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

    # Auto-resolve reference gaps against current library
    if ctx.library:
        resolved = state.resolve_gaps(ctx.library.entries)
        if resolved:
            result["gaps_resolved"] = resolved

    # Dual-write section assignments to project.db (ADR-014 Phase 1D)
    if ctx.project_store and ctx.user_library:
        for vd in vault_data:
            if not vd.get("sections"):
                continue
            paper_id = (
                ctx.user_library.resolve_paper_id(vd["citekey"])
                or f"migrated:{vd['citekey']}"
            )
            ctx.project_store.set_source_sections(
                vd["citekey"],
                paper_id,
                vd["sections"],
                vd.get("chapters") or [],
            )

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
        if result.get("gaps_resolved"):
            parts.append(f"[green]{result['gaps_resolved']} gap(s) resolved[/green]")
        if parts:
            console.print("[dim]Sync:[/dim] " + " | ".join(parts))

    return result


def _print_status_line(
    state: StateManager,
    project_name: str = "default",
    model: str = "",
    db_label: str = "",
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
        if db_label:
            parts.append(f"[dim]{db_label}[/dim]")
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
                "klemma library prune",
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

    # Initialize project context if a subcommand is being run.
    # Skip for init/info/tree/migrate which bootstrap a project.
    # The try/except lets --help and similar eager options work in non-project dirs:
    # Click processes --help BEFORE invoking the subcommand callback, so _get_context()
    # is never called for --help, making the silent swallow of ClickException safe.
    skip_check = {"init", "info", "tree", "migrate"}
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
            _ps = kctx.project_store
            db_label = (
                "data/project.db"
                if _ps and _ps.count_sources() > 0
                else "data/klemma.db"
            )
            _print_status_line(
                kctx.state,
                project_name=kctx.project_name,
                model=effective_model,
                db_label=db_label,
            )
        except Exception:
            pass

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


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


def _read_multiline_input() -> str:
    """Read multi-line input until double-Enter or EOF."""
    lines: list[str] = []
    empty_count = 0
    try:
        while True:
            line = input("    ")
            if not line.strip():
                empty_count += 1
                if empty_count >= 2:
                    break
                lines.append("")
            else:
                empty_count = 0
                lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines).rstrip()


def _detect_openai_key(prefill: dict | None) -> str:
    """Auto-detect OpenAI API key from env vars, klemmarc, or parent config.

    Returns the key string if found, empty string otherwise.
    """
    import os

    # 1. Check environment variable
    env_key = os.environ.get("OPENAI_API_KEY", "")
    if env_key:
        return env_key

    # 2. Check klemmarc
    try:
        import yaml as _yaml

        from .setup import _find_klemmarc

        klemmarc = _find_klemmarc(Path.home())
        if klemmarc:
            krc = _yaml.safe_load(klemmarc.read_text(encoding="utf-8")) or {}
            krc_key = krc.get("api_keys", {}).get("openai", "")
            if krc_key:
                return krc_key
    except Exception:
        pass

    # 3. Check parent project config (config cascade)
    try:
        from .config import discover_project_chain, resolve_effective_config

        chain = discover_project_chain(Path.cwd())
        if chain:
            cfg = resolve_effective_config(chain)
            resolved = getattr(cfg.ai, "_resolved_api_keys", {})
            parent_key = resolved.get("openai", "")
            if parent_key:
                return parent_key
    except Exception:
        pass

    return ""


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

    # --- Step 1: Project type ---
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

    # --- Step 2: Project outline ---
    click.echo()
    click.echo("  Project outline")
    click.echo("  " + "─" * 50)
    click.echo("  Klemma uses the outline to build chapter/section")
    click.echo("  structure for your project. All subsequent commands")
    click.echo("  (research, process, draft) use this structure to")
    click.echo("  filter sources and generate section-targeted content.")
    click.echo("  Formats: .docx, .md, .txt — or type text directly.")
    click.echo()

    plan_data = pf.get("_plan_data")  # set when --plan was passed
    if plan_data:
        click.echo(f"  Outline loaded: {plan_data.title[:70]}...")
        click.echo(
            f"    Chapters: {len(plan_data.chapters)}, "
            f"НР: {len(plan_data.results)}, "
            f"Tasks: {len(plan_data.tasks)}"
        )
    else:
        outline_path_str = click.prompt(
            "  File path (.docx, .md, .txt) — or empty to type/skip",
            default="",
            show_default=False,
        )
        if outline_path_str:
            outline_file = Path(outline_path_str.strip()).expanduser()
            if outline_file.exists():
                try:
                    from .plan_parser import parse_file

                    plan_data = parse_file(outline_file)
                    if plan_data.title:
                        click.echo(f"    Parsed: {plan_data.title[:70]}...")
                    if plan_data.chapters:
                        click.echo(
                            f"    Chapters: {len(plan_data.chapters)}, "
                            f"НР: {len(plan_data.results)}, "
                            f"Tasks: {len(plan_data.tasks)}"
                        )
                    else:
                        click.echo("    No chapter structure detected — stored as context")
                except ImportError:
                    click.echo(
                        "    [warning] python-docx not installed: pip install python-docx"
                    )
                except Exception as e:
                    click.echo(f"    [warning] Could not parse: {e}")
            else:
                click.echo(f"    [warning] File not found: {outline_file}")

        if not plan_data:
            if click.confirm("  Type or paste outline text?", default=False):
                click.echo("    Enter text below. Press Enter twice to finish.")
                raw_text = _read_multiline_input()
                if raw_text:
                    from .plan_parser import parse_text

                    plan_data = parse_text(raw_text)
                    if plan_data.chapters:
                        click.echo(f"    Parsed: {len(plan_data.chapters)} chapters")
                    elif plan_data.title:
                        click.echo(f"    Title: {plan_data.title[:70]}")
                    else:
                        click.echo("    Stored as project context")
                    # If no chapters detected, store raw text as description
                    if not plan_data.chapters and not description:
                        description = raw_text[:500]

    # Use plan title if available and user didn't provide one
    if plan_data and plan_data.title and not title:
        title = plan_data.title
        click.echo(f"  Title (from outline): {title[:80]}")

    language = click.prompt(
        "  AI language",
        default=pf.get("language", "ru"),
    )

    # --- Step 3: AI setup ---
    # Auto-detect OpenAI key from env / klemmarc / parent config
    click.echo("\n  AI setup")
    detected_key = _detect_openai_key(pf)
    openai_api_key = ""

    if detected_key:
        # Key found — show masked version and skip the question
        masked = detected_key[:7] + "..." + detected_key[-4:]
        click.echo(f"  + OpenAI API key detected: {masked}")
        openai_api_key = detected_key
        has_openai = True
    else:
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
    # Default: LiteLLM + Ollama/BGE-M3 (free, local, single Anthropic billing).
    # Users can downgrade to OpenAI embeddings only when they explicitly say so.
    embeddings_backend = "litellm"
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
        else:
            backend = "litellm"
            ai_model = "openai/gpt-4.1"

        click.echo("\n  Embeddings backend")
        click.echo("    1. LiteLLM + Ollama (bge-m3) — free, offline, strong on Russian")
        click.echo("    2. OpenAI text-embedding-3-small (uses the key above)")
        emb_choice = click.prompt(
            "  Choose",
            type=click.Choice(["1", "2"]),
            default="1",
        )
        if emb_choice == "1":
            embeddings_backend = "litellm"
            click.echo(f"    LLM: {ai_model}  |  Embeddings: LiteLLM + Ollama (bge-m3)")
            click.echo("    (one-time setup: ollama pull bge-m3)")
        else:
            embeddings_backend = "openai"
            click.echo(f"    LLM: {ai_model}  |  Embeddings: OpenAI text-embedding-3-small")
    else:
        backend = "claude"
        ai_model = "sonnet"
        click.echo(
            "    LLM: Claude Code Max  |  Embeddings: LiteLLM + Ollama (bge-m3)"
        )
        click.echo("    (one-time setup: ollama pull bge-m3)")

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


def _coach_section_hint(state, section: str, project_root=None) -> str | None:
    """Generate 1-line coach hint for a section. Returns None if nothing to say."""
    from .skills.coach import coach_section_hint

    coverage = state.get_coverage_stats()
    source_count = coverage.get("sections", {}).get(section, 0)
    intent = state.get_intent_coverage().get(section, {})
    frags = state.get_fragments(section=section)
    level = "chapter" if "." not in section else "subsection"
    has_draft = bool(
        project_root
        and (project_root / "notes" / "drafts" / f"Draft_{section}.md").exists()
    )
    return coach_section_hint(
        section=section,
        source_count=source_count,
        level=level,
        intent_counts=intent,
        fragment_count=len(frags),
        has_draft=has_draft,
    )


def _auto_embed_after_process(
    citekey,
    state,
    embeddings,
    quiet=False,
    paper_store=None,
    user_library=None,
):
    """Embed fragments + recompute section centroids for a just-processed source.

    Returns total embeddings created.
    """
    from .hashing import compute_content_hash

    count = 0

    # Resolve library fragment cache for this citekey
    _paper_id = None
    _lib_cache: dict[str, list[float]] = {}
    if paper_store and user_library:
        try:
            _paper_id = user_library.resolve_paper_id(citekey)
            if _paper_id:
                _lib_cache = paper_store.get_fragment_embeddings(
                    _paper_id, embeddings.model_name
                )
        except Exception:
            pass

    # Fragment embeddings
    fragments = state.get_fragments(source_id=citekey)
    for frag in fragments:
        if frag.get("embedding"):  # already embedded
            continue

        # Library cache check
        if _paper_id and _lib_cache:
            ch = compute_content_hash(
                _paper_id, frag["fragment_text"], frag.get("page_number")
            )
            if ch in _lib_cache:
                state.save_fragment_embedding(frag["id"], _lib_cache[ch], embeddings.model_name)
                count += 1
                continue

        try:
            vec = embeddings.embed(frag["fragment_text"])
            if vec:
                state.save_fragment_embedding(frag["id"], vec, embeddings.model_name)
                count += 1
                # Write-through to library.db
                if _paper_id and paper_store:
                    try:
                        ch = compute_content_hash(
                            _paper_id, frag["fragment_text"], frag.get("page_number")
                        )
                        paper_store.save_fragment_embedding(ch, vec, embeddings.model_name)
                    except Exception:
                        pass
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
    paper_store=None,
    user_library=None,
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

    # Online source: fetch URL text instead of PDF
    source_type = source.get("source_type", "") if source else ""
    if source_type == "online":
        source_url = source.get("url", "") if source else ""
        if not source_url:
            if not quiet:
                console.print("  [red]Online source has no URL[/red]")
            state.sources.mark_skipped(citekey, "online source missing URL")
            return (0, "online source missing URL")
        if not quiet:
            console.print(f"  [dim]Fetching URL: {source_url[:80]}[/dim]")
        from .literature.web import fetch_url_text
        pdf_text = fetch_url_text(source_url, max_chars=cfg.ai.max_pdf_chars)
        if not pdf_text or len(pdf_text) < 100:
            if not quiet:
                console.print("  [red]URL fetch failed or content too short[/red]")
            state.sources.mark_skipped(citekey, "URL fetch failed")
            return (0, "URL fetch failed")
    else:
        # Phase 1C citekey fast-path dedup: check user_library before reading PDF (ADR-014)
        # Faster than pdf_hash check: no PDF read needed when same citekey already processed.
        if user_library and paper_store and not force:
            try:
                _fast_paper_id = user_library.resolve_paper_id(citekey)
                if _fast_paper_id:
                    _fast_frags = paper_store.get_fragments(_fast_paper_id)
                    if _fast_frags:
                        frag_dicts = [
                            {
                                "text": f.fragment_text,
                                "type": f.fragment_type or "key_idea",
                                "page": f.page_number,
                                "citation_intent": f.citation_intent,
                                "relevance": 3,
                            }
                            for f in _fast_frags
                        ]
                        n = state.fragments.save_fragments(citekey, frag_dicts)
                        _existing_src = state.sources.get_source(citekey)
                        _note_path = (_existing_src or {}).get("note_path") or ""
                        state.sources.mark_completed(citekey, note_path=_note_path)
                        if not quiet:
                            console.print(
                                f"  [green]{n} fragments[/green] "
                                f"[dim](library cache — skipped PDF)[/dim]"
                            )
                        if embeddings and not no_embed:
                            _auto_embed_after_process(
                                citekey, state, embeddings, quiet=quiet,
                                paper_store=paper_store, user_library=user_library,
                            )
                        return (n, "ok")
            except Exception as _e:
                logger.debug("Citekey dedup check failed for %s: %s", citekey, _e)

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

        # Phase 1B dedup: check library.db before extracting (ADR-014)
        _pdf_hash = None
        _paper_id = None
        if paper_store and not force:
            try:
                from .hashing import compute_pdf_hash

                _pdf_hash = compute_pdf_hash(pdf_path)
                paper_rec = paper_store.find_paper(pdf_hash=_pdf_hash)
                if paper_rec:
                    _paper_id = paper_rec.paper_id
                    lib_frags = paper_store.get_fragments(_paper_id)
                    if lib_frags:
                        frag_dicts = [
                            {
                                "text": f.fragment_text,
                                "type": f.fragment_type or "key_idea",
                                "page": f.page_number,
                                "citation_intent": f.citation_intent,
                                "relevance": 3,
                            }
                            for f in lib_frags
                        ]
                        n = state.fragments.save_fragments(citekey, frag_dicts)
                        _existing_src = state.sources.get_source(citekey)
                        _note_path = (_existing_src or {}).get("note_path") or ""
                        state.sources.mark_completed(citekey, note_path=_note_path)
                        if not quiet:
                            console.print(
                                f"  [green]{n} fragments[/green] "
                                f"[dim](library cache — Claude skipped)[/dim]"
                            )
                        if embeddings and not no_embed:
                            _auto_embed_after_process(
                                citekey, state, embeddings, quiet=quiet,
                                paper_store=paper_store, user_library=user_library,
                            )
                        return (n, "ok")
            except Exception as _e:
                logger.debug("Library dedup check failed for %s: %s", citekey, _e)

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

    # Phase 1B dual-write: also persist to library.db (ADR-014)
    # online sources have no PDF hash — skip dual-write
    if paper_store and not force and source_type != "online":
        try:
            from .hashing import compute_content_hash, compute_prompt_hash
            from .models import FragmentRecord

            # _pdf_hash/_paper_id always initialized at top of dedup block above;
            # reuse them if already computed, otherwise compute now.
            if _pdf_hash is None and pdf_path:
                from .hashing import compute_pdf_hash

                _pdf_hash = compute_pdf_hash(pdf_path)
            if _pdf_hash and _paper_id is None:
                _paper_id = paper_store.register_paper(
                    title=entry.title or citekey,
                    authors=entry.authors_str or "",
                    year=entry.year,
                    doi=getattr(entry, "doi", None) or None,
                    abstract=entry.abstract or "",
                    pdf_hash=_pdf_hash,
                )
            if _paper_id:
                lib_records = [
                    FragmentRecord(
                        fragment_id=compute_content_hash(_paper_id, f.text, f.page),
                        paper_id=_paper_id,
                        fragment_text=f.text,
                        fragment_type=f.type,
                        page_number=f.page,
                        citation_intent=f.citation_intent,
                        content_hash=compute_content_hash(_paper_id, f.text, f.page),
                    )
                    for f in result.fragments
                ]
                p_hash = compute_prompt_hash(cfg.ai.model or "unknown")
                paper_store.save_fragments(
                    _paper_id, lib_records, p_hash, cfg.ai.model or "unknown"
                )
                logger.debug(
                    "Library dual-write: %d fragments for %s", len(lib_records), citekey
                )
        except Exception as _e:
            logger.debug("Library dual-write failed for %s: %s", citekey, _e)

    # Phase 1C: register citekey → paper_id in user library
    if user_library and _paper_id:
        try:
            user_library.add_source(
                _paper_id,
                citekey,
                status="completed",
                pdf_path=str(pdf_path) if pdf_path else None,
            )
            logger.debug("User library: registered %s → %s", citekey, _paper_id)
        except Exception as _e:
            logger.debug("User library registration failed for %s: %s", citekey, _e)

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
        _auto_embed_after_process(
            citekey, state, embeddings, quiet=quiet,
            paper_store=paper_store, user_library=user_library,
        )

    return (len(result.fragments), "ok")


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
            "Set embeddings.backend in config.yaml (s2, local, openai, litellm) "
            "or use --backend flag."
        )
        return None
    return emb


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


# --- Add: unified source ingestion ---


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
        from pathlib import Path as _Path

        pdf_path = _Path(input_value).resolve()
        console.print(f"[bold]Adding from PDF:[/bold] {pdf_path.name}")
        # Use acquire with file:// URL — acquirer handles local paths via scheme check
        meta = PaperMetadata(
            url=f"file://{pdf_path}",
            title=title or "",
            authors=authors or "",
            year=year,
            sections=sections,
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
            # Not in DB — check if it exists in Zotero library and auto-register
            if kctx.library and citekey in kctx.library.entries:
                state.register_sources([citekey])
                console.print(f"[bold]Registering:[/bold] @{citekey}")
            else:
                console.print(f"[red]Source @{citekey} not found in database or library.[/red]")
                console.print("[dim]Use a URL or PDF path to add a new source.[/dim]")
                return
        else:
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

    # --- Coach hint ---
    if sections and citekey:
        for sec in sections:
            hint = _coach_section_hint(state, sec, kctx.project_root)
            if hint:
                console.print(f"[dim]\U0001f4a1 {hint}[/dim]")
                break  # one hint is enough


# --- Coach: research advisor ---


@main.command()
@click.option("--section", "-s", help="Focus on a specific section")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.pass_context
def coach(ctx, section, json_output):
    """Research advisor \u2014 methodology-driven guidance (zero AI calls)."""
    import json as json_mod

    from .skills.coach import CoachReport, analyze_project, analyze_section

    kctx = _get_context(ctx)
    state = kctx.state
    _sync_sections(kctx)

    if section:
        # Section focus mode — use get_coverage_stats for parent-aware count
        coverage = state.get_coverage_stats()
        source_count = coverage.get("sections", {}).get(section, 0)
        intent = state.get_intent_coverage().get(section, {})
        frags = state.get_fragments(section=section)
        level = "chapter" if "." not in section else "subsection"
        has_draft = bool(
            kctx.project_root
            and (
                kctx.project_root / "notes" / "drafts" / f"Draft_{section}.md"
            ).exists()
        )
        findings = analyze_section(
            section=section,
            source_count=source_count,
            level=level,
            intent_counts=intent,
            fragment_count=len(frags),
            has_draft=has_draft,
        )
        report = CoachReport(findings=findings, section=section)
    else:
        # Project-wide health check
        coverage = state.get_coverage_stats()
        intent_coverage = state.get_intent_coverage()
        fragment_stats = state.get_fragment_embedding_stats()
        gap_summary = state.get_gap_summary()
        sections_map = coverage.get("sections", {})
        section_levels = {
            s: ("chapter" if "." not in s else "subsection")
            for s in sections_map
        }
        drafts: set[str] = set()
        if kctx.project_root:
            drafts_dir = kctx.project_root / "notes" / "drafts"
            if drafts_dir.exists():
                for f in drafts_dir.glob("Draft_*.md"):
                    drafts.add(f.stem.replace("Draft_", ""))
        report = analyze_project(
            coverage_stats=coverage,
            intent_coverage=intent_coverage,
            fragment_stats=fragment_stats,
            gap_summary=gap_summary,
            section_levels=section_levels,
            drafts=drafts,
        )

    # Output
    if json_output:
        data = {
            "section": report.section,
            "findings": [
                {
                    "category": f.category,
                    "section": f.section,
                    "message": f.message,
                    "severity": f.severity,
                }
                for f in report.findings
            ],
        }
        click.echo(json_mod.dumps(data, indent=2))
        return

    if not report.findings:
        console.print("[green]All sections look good.[/green]")
        return

    for f in report.findings:
        style = {"action": "bold red", "warning": "yellow", "info": "dim"}.get(
            f.severity, ""
        )
        prefix = {"action": "\u2192", "warning": "\u26a0", "info": "\u2139"}.get(
            f.severity, "\u2022"
        )
        console.print(f"  [{style}]{prefix} {f.message}[/{style}]")


# Register CLI commands from submodules (must be at bottom to avoid circular imports)
from .commands import (  # noqa: E402, F401
    acquire,
    analyze,
    benchmark,
    bib,
    manage,
    process,
    research,
    write,
)

if __name__ == "__main__":
    main()
