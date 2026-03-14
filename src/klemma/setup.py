"""Setup logic for `klemma init` — creates .klemma/ project in current directory.

Two modes:
- init_project(): creates .klemma/ + KLEMMA.md in a project directory
- init_system(): creates ~/.klemma/ with minimal global config
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

# Example files shipped with the package (repo root)
_EXAMPLES_DIR = Path(__file__).parent.parent.parent


@dataclass
class InitValues:
    """Values collected by the interactive wizard (or auto-discovery)."""

    project_type: str = "dissertation"
    title: str = ""
    description: str = ""
    keywords: list[str] = None  # type: ignore[assignment]
    language: str = "ru"
    vault_path: str = ""
    notes_folder: str = ""
    tags_folder: str = ""
    zotero_storage: str = ""
    zotero_library_json: str = ""
    backend: str = ""          # "claude" | "litellm" | "" (use klemmarc default)
    ai_model: str = ""         # model name for project config (e.g. "sonnet", "openai/gpt-4.1")
    openai_api_key: str = ""   # OpenAI key to save in ~/.klemmarc.yaml
    embeddings_backend: str = ""  # "openai" | "s2" | "" (derive from openai_api_key)
    plan_data: Any = None         # PlanData from plan_parser (transient, not serialized)

    def __post_init__(self):
        if self.keywords is None:
            self.keywords = []


def _build_project_config(values: InitValues, *, has_parent: bool = False) -> dict:
    """Build config dict from wizard values.

    Content fields (chapters, title, etc.) now live in KLEMMA.md frontmatter.
    This function returns infrastructure-only config (ai, zotero, obsidian, state).

    When has_parent=True, skip ai/embeddings defaults — child projects
    inherit these from parent via config cascade (ADR-012).
    Only write language override and explicitly provided values.
    """
    cfg: dict = {}

    # Zotero (only if paths provided)
    zotero: dict = {}
    if values.zotero_library_json:
        zotero["library_json"] = values.zotero_library_json
    if values.zotero_storage:
        zotero["storage_path"] = values.zotero_storage
    if zotero:
        cfg["zotero"] = zotero

    # Obsidian (only if vault provided)
    if values.vault_path:
        obsidian: dict = {"vault_path": values.vault_path}
        if values.notes_folder:
            obsidian["notes_folder"] = values.notes_folder
        if values.tags_folder:
            obsidian["tags_folder"] = values.tags_folder
        cfg["obsidian"] = obsidian

    # AI — skip backend/model defaults for child projects (inherited from parent)
    if has_parent:
        ai_cfg: dict = {"language": values.language}
        # Only write backend/model if explicitly provided by user
        if values.backend:
            ai_cfg["backend"] = values.backend
        if values.ai_model:
            ai_cfg["model"] = values.ai_model
        cfg["ai"] = ai_cfg
    else:
        # Root project — write full AI config
        ai_cfg = {"language": values.language}
        if values.backend:
            ai_cfg["backend"] = values.backend
        if values.ai_model:
            ai_cfg["model"] = values.ai_model
        elif values.backend == "claude":
            ai_cfg["model"] = "sonnet"
        elif values.backend == "litellm":
            ai_cfg["model"] = "anthropic/claude-sonnet-4-6"
        else:
            # No backend chosen — use shorthand, klemmarc will define backend
            ai_cfg["model"] = "sonnet"
        cfg["ai"] = ai_cfg

    # Embeddings — only written when explicitly configured or OpenAI key present.
    # Do NOT default to s2 — user should opt in explicitly.
    emb_backend = values.embeddings_backend
    if not emb_backend and values.openai_api_key:
        emb_backend = "openai"
    if emb_backend == "openai":
        cfg["embeddings"] = {"backend": "openai", "model": "text-embedding-3-small"}
    elif emb_backend == "s2":
        cfg["embeddings"] = {"backend": "s2"}

    return cfg


def _build_dissertation_config(plan_data) -> dict:
    """Build dissertation config section from parsed PlanData."""
    from .section_types import infer_section_type

    diss: dict = {}

    # Title
    if plan_data.title:
        diss["title"] = plan_data.title

    # Chapters: {1: "Chapter name", 2: "Chapter name", ...}
    if plan_data.chapters:
        chapters = {}
        for ch in plan_data.chapters:
            chapters[ch.number] = ch.title
        diss["chapters"] = chapters

        # Section type map: infer from chapter names
        section_type_map = {}
        for ch in plan_data.chapters:
            inferred = infer_section_type(ch.title)
            if inferred:
                section_type_map[str(ch.number)] = inferred.value
        if section_type_map:
            diss["section_type_map"] = section_type_map

        # Current chapter defaults to 1
        diss["current_chapter"] = 1
        if plan_data.chapters[0].sections:
            diss["current_section"] = plan_data.chapters[0].sections[0].number

    # Scientific results: {nr1: "title", nr2: "title", ...}
    if plan_data.results:
        nr_map = {}
        for nr in plan_data.results:
            nr_map[f"nr{nr.number}"] = nr.title
        diss["scientific_results"] = nr_map

    # Min sources per section
    diss["min_sources_per_section"] = 3

    # Auto-generate chapter_mapping from chapter titles
    if diss.get("chapters"):
        from .config import generate_chapter_mapping
        sections_dict: dict[str, str] = {}
        for ch in plan_data.chapters:
            for sec in ch.sections:
                sections_dict[sec.number] = sec.title
        mapping = generate_chapter_mapping(
            {int(k): v for k, v in diss["chapters"].items()},
            sections_dict or None,
        )
        if mapping:
            diss["chapter_mapping"] = [
                {"pattern": m.pattern, "chapter": m.chapter, "section": m.section}
                for m in mapping
            ]

    return diss


def _build_klemma_md(values: InitValues) -> str:
    """Build KLEMMA.md content with YAML frontmatter from wizard values.

    Content fields go in frontmatter; human-readable prose goes in markdown body.
    """
    import yaml as _yaml

    title = values.title or "Your project title here"

    # Build frontmatter dict (content fields only)
    frontmatter: dict = {"type": values.project_type, "title": title}

    if values.description:
        frontmatter["description"] = values.description
    if values.keywords:
        frontmatter["priority_terms"] = values.keywords

    # Dissertation structure from plan-prospect → into frontmatter
    if values.plan_data and values.project_type == "dissertation":
        diss = _build_dissertation_config(values.plan_data)
        if "chapters" in diss:
            frontmatter["chapters"] = diss["chapters"]
        if "section_type_map" in diss:
            frontmatter["section_type_map"] = diss["section_type_map"]
        if "scientific_results" in diss:
            frontmatter["scientific_results"] = diss["scientific_results"]
        if "current_section" in diss:
            frontmatter["current_focus"] = diss["current_section"]
        if "min_sources_per_section" in diss:
            frontmatter["min_sources_per_section"] = diss["min_sources_per_section"]
        if "chapter_mapping" in diss:
            frontmatter["chapter_mapping"] = diss["chapter_mapping"]
        if not frontmatter.get("title") and "title" in diss:
            frontmatter["title"] = diss["title"]
    else:
        # Defaults for non-plan projects
        frontmatter["current_focus"] = "1.1"
        frontmatter["chapters"] = {
            1: "Literature review",
            2: "Methodology",
            3: "Results and discussion",
        }
        frontmatter["scientific_results"] = {
            "nr1": "First scientific result",
            "nr2": "Second scientific result",
        }

    frontmatter["min_sources_per_section"] = frontmatter.get("min_sources_per_section", 3)
    frontmatter["auto_register"] = "mapped"

    fm_text = _yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # Build markdown body (prose context for AI)
    nr_lines = ""
    if "scientific_results" in frontmatter:
        nrs = frontmatter["scientific_results"]
        nr_lines = "\n".join(f"- {k.upper()}: {v}" for k, v in nrs.items())

    ch_lines = ""
    if "chapters" in frontmatter:
        ch_lines = "\n".join(
            f"{num}. {name}" for num, name in sorted(frontmatter["chapters"].items())
        )

    kw_line = ""
    if values.keywords:
        kw_line = f"\nKey terms: {', '.join(values.keywords)}"

    desc_line = f"\nDescription: {values.description}" if values.description else ""

    body = (
        "# Project Context\n\n"
        "<!-- This file describes your project for AI. It is passed as context to all\n"
        "     AI-powered commands (plan, research, process, ask, etc.).\n\n"
        "     YAML frontmatter above holds structured project data. The markdown body\n"
        "     below is free-form context shown to AI. Keep both in sync. -->\n\n"
        f'Topic: "{title}"\n'
        f"{desc_line}\n\n"
        "## Scientific Results\n\n"
        f"{nr_lines}\n\n"
        "## Structure\n\n"
        f"{ch_lines}\n"
        f"{kw_line}\n"
    )

    return f"---\n{fm_text}---\n{body}"


def _setup_claude_skills(project_dir: Path, created: list[str], skipped: list[str]):
    """Symlink Claude Code skills from the klemma package into the project."""
    source_skills = _EXAMPLES_DIR / ".claude" / "skills"
    if not source_skills.is_dir():
        return

    target_dir = project_dir / ".claude" / "skills"
    target_dir.mkdir(parents=True, exist_ok=True)

    for skill_dir in source_skills.iterdir():
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        target = target_dir / skill_dir.name
        if target.exists() or target.is_symlink():
            skipped.append(f".claude/skills/{skill_dir.name}")
        else:
            target.symlink_to(skill_dir)
            created.append(f".claude/skills/{skill_dir.name}")


_GITIGNORE_TEMPLATE = (
    "# Klemma data (SQLite DBs — not for version control)\n"
    "**/.klemma/data/\n"
    "**/.klemma/state.db\n"
    "\n"
    "# macOS\n"
    ".DS_Store\n"
    "\n"
    "# LaTeX build artifacts\n"
    "*.aux\n"
    "*.log\n"
    "*.blg\n"
    "*.bbl\n"
    "*.out\n"
    "*.fls\n"
    "*.fdb_latexmk\n"
    "*.synctex.gz\n"
    "\n"
    "# Python\n"
    "__pycache__/\n"
    "*.pyc\n"
)

_GITIGNORE_PATTERNS = [
    "**/.klemma/data/",
    "**/.klemma/state.db",
    ".DS_Store",
    "*.aux",
    "__pycache__/",
]


def init_project(
    project_dir: Path,
    project_type: str = "dissertation",
    values: Optional[InitValues] = None,
    *,
    has_parent: bool = False,
) -> dict:
    """Create .klemma/ project in project_dir + KLEMMA.md.

    If values is provided (interactive mode), writes config from discovered/prompted
    values. Otherwise falls back to example template (--no-input / legacy).

    When has_parent=True, child project config skips ai/embeddings defaults —
    these are inherited from parent via config cascade (ADR-012).

    Returns dict with keys: created (list of file names), skipped (list).
    """
    created: list[str] = []
    skipped: list[str] = []

    klemma_dir = project_dir / ".klemma"
    klemma_dir.mkdir(parents=True, exist_ok=True)
    (klemma_dir / "data").mkdir(exist_ok=True)

    # Project config
    config_target = klemma_dir / "config.yaml"
    if config_target.exists():
        skipped.append(".klemma/config.yaml")
    else:
        if values:
            values.project_type = project_type
            cfg = _build_project_config(values, has_parent=has_parent)
            text = yaml.dump(cfg, default_flow_style=False, allow_unicode=True, sort_keys=False)
            config_target.write_text(text, encoding="utf-8")
        else:
            source = _EXAMPLES_DIR / "config.project.example.yaml"
            if source.exists():
                text = source.read_text(encoding="utf-8")
                text = text.replace("type: dissertation", f"type: {project_type}")
                config_target.write_text(text, encoding="utf-8")
            else:
                config_target.write_text(
                    f"# Klemma project config\n"
                    f"project:\n"
                    f"  type: {project_type}\n"
                    f"  title: \"\"\n",
                    encoding="utf-8",
                )
        created.append(".klemma/config.yaml")

    # Tags
    tags_target = klemma_dir / "tags.yaml"
    if tags_target.exists():
        skipped.append(".klemma/tags.yaml")
    else:
        source = _EXAMPLES_DIR / "tags.example.yaml"
        if source.exists():
            shutil.copy2(source, tags_target)
        else:
            tags_target.write_text("# Project tags\n- Review\n- Methodology\n", encoding="utf-8")
        created.append(".klemma/tags.yaml")

    # KLEMMA.md context file (in project root, visible to user)
    klemma_md = project_dir / "KLEMMA.md"
    if klemma_md.exists():
        skipped.append("KLEMMA.md")
    else:
        if values:
            klemma_md.write_text(_build_klemma_md(values), encoding="utf-8")
        else:
            source = _EXAMPLES_DIR / "klemma.example.md"
            if source.exists():
                from .config import parse_klemma_md as _parse, save_klemma_md as _save  # noqa: I001
                fm, body = _parse(source)
                if fm:
                    fm["type"] = project_type
                    _save(klemma_md, fm, body)
                else:
                    # No frontmatter in example — copy as-is
                    shutil.copy2(source, klemma_md)
            else:
                klemma_md.write_text(
                    f"# Project Context\n\n"
                    f"Type: {project_type}\n"
                    f"Title: \"\"\n\n"
                    f"<!-- Describe your project here. This context is passed to AI. -->\n",
                    encoding="utf-8",
                )
        created.append("KLEMMA.md")

    # Claude Code skills (symlink from package)
    _setup_claude_skills(project_dir, created, skipped)

    # .gitignore: exclude DBs and build artifacts, keep config in VCS
    gitignore = project_dir / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        missing = [p for p in _GITIGNORE_PATTERNS if p not in content]
        if missing:
            with open(gitignore, "a", encoding="utf-8") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write("# Added by klemma init\n")
                f.write("\n".join(missing) + "\n")
            created.append(".gitignore (updated)")
    else:
        gitignore.write_text(_GITIGNORE_TEMPLATE, encoding="utf-8")
        created.append(".gitignore")

    return {"created": created, "skipped": skipped}


_KLEMMARC_TEMPLATE = """\
# =============================================================================
# KLEMMA GLOBAL CONFIG — ~/.klemmarc.yaml
# =============================================================================
# Single config file for all klemma projects. Overridden by project configs.
# This file has 0600 permissions because it may contain API keys.

ai:
  backend: "litellm"                   # recommended: litellm (100+ providers)
  model: "anthropic/claude-sonnet-4-6" # litellm model format: provider/model
  language: "ru"
  timeout: 300
  retries: 2

# Direct API keys — no more env var juggling.
# Uncomment and fill in the keys you need:
# api_keys:
#   openai: "sk-..."
#   anthropic: "sk-ant-..."
#   google: "AIza..."

# embeddings:
#   backend: "openai"
#   model: "text-embedding-3-small"

# Override the default library.db location (default: ~/.klemma/library.db).
# Useful for placing the shared corpus on a faster/larger drive, or on a
# network share that multiple machines can access simultaneously.
# library_db_path: "~/Dropbox/klemma/library.db"
"""

_KLEMMARC_NAMES = (".klemmarc.yaml", ".klemmarc.yml", ".klemmarc")


def _build_klemmarc(values: Optional["InitValues"] = None) -> str:
    """Build ~/.klemmarc.yaml content, optionally pre-configured from wizard values."""
    if not values or not values.backend:
        return _KLEMMARC_TEMPLATE

    lines = [
        "# =============================================================================",
        "# KLEMMA GLOBAL CONFIG — ~/.klemmarc.yaml",
        "# =============================================================================",
        "# Single config file for all klemma projects. Overridden by project configs.",
        "# This file has 0600 permissions because it may contain API keys.",
        "",
    ]

    if values.backend == "claude":
        lines += [
            "ai:",
            '  backend: "claude"                    # Claude Code Max (no API key needed)',
            '  model: "sonnet"                      # claude shorthand: sonnet, opus',
            f'  language: "{values.language}"',
            "  timeout: 300",
            "  retries: 2",
        ]
    elif values.backend == "litellm":
        model = values.ai_model or "openai/gpt-4.1"
        lines += [
            "ai:",
            '  backend: "litellm"                   # recommended: litellm (100+ providers)',
            f'  model: "{model}"',
            f'  language: "{values.language}"',
            "  timeout: 300",
            "  retries: 2",
        ]
    else:
        lines += [
            "ai:",
            f'  backend: "{values.backend}"',
            f'  model: "{values.ai_model or "sonnet"}"',
            f'  language: "{values.language}"',
            "  timeout: 300",
            "  retries: 2",
        ]

    lines.append("")

    # API keys
    if values.openai_api_key:
        lines += [
            "api_keys:",
            f'  openai: "{values.openai_api_key}"',
            "#   anthropic: \"sk-ant-...\"",
            "#   google: \"AIza...\"",
        ]
    else:
        lines += [
            "# Direct API keys — no more env var juggling.",
            "# Uncomment and fill in the keys you need:",
            "# api_keys:",
            '#   openai: "sk-..."',
            '#   anthropic: "sk-ant-..."',
            '#   google: "AIza..."',
        ]

    lines.append("")

    # Embeddings — active if OpenAI key provided, commented out otherwise
    if values.openai_api_key:
        lines += [
            "embeddings:",
            '  backend: "openai"',
            '  model: "text-embedding-3-small"',
        ]
    else:
        lines += [
            "# Embeddings (requires OpenAI key):",
            "# embeddings:",
            '#   backend: "openai"',
            '#   model: "text-embedding-3-small"',
            "#",
            "# Free alternative (no key needed, slower):",
            "# embeddings:",
            '#   backend: "s2"',
        ]

    lines.append("")
    return "\n".join(lines)


def _inject_klemmarc_api_key(home: Path, provider: str, key: str) -> None:
    """Add or update an API key in existing ~/.klemmarc.yaml."""
    klemmarc_path = _find_klemmarc(home)
    if not klemmarc_path:
        return

    raw = yaml.safe_load(klemmarc_path.read_text(encoding="utf-8")) or {}
    raw.setdefault("api_keys", {})[provider] = key
    klemmarc_path.write_text(
        yaml.dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _update_klemmarc_backend(home: Path, values: "InitValues") -> None:
    """Update backend, model, and embeddings in existing ~/.klemmarc.yaml."""
    klemmarc_path = _find_klemmarc(home)
    if not klemmarc_path:
        return

    raw = yaml.safe_load(klemmarc_path.read_text(encoding="utf-8")) or {}
    ai = raw.setdefault("ai", {})
    ai["backend"] = values.backend
    if values.backend == "claude":
        ai["model"] = values.ai_model or "sonnet"
    elif values.ai_model:
        ai["model"] = values.ai_model

    # Embeddings: enable OpenAI if key provided, otherwise leave as-is
    if values.openai_api_key and "embeddings" not in raw:
        raw["embeddings"] = {"backend": "openai", "model": "text-embedding-3-small"}

    klemmarc_path.write_text(
        yaml.dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _find_klemmarc(home: Path) -> Optional[Path]:
    """Find the active klemmarc file."""
    for name in _KLEMMARC_NAMES:
        p = home / name
        if p.exists():
            return p
    return None


def init_system(
    system_home: Path,
    values: Optional["InitValues"] = None,
) -> dict:
    """Create ~/.klemma/ system directory with global config + ~/.klemmarc.yaml.

    If values is provided and contains backend/api_key info, writes them into
    the klemmarc template. Updates existing klemmarc if only api_keys change.

    Returns dict with keys: created, skipped.
    """
    created: list[str] = []
    skipped: list[str] = []

    system_home.mkdir(parents=True, exist_ok=True)

    # ~/.klemmarc.yaml — new global config with api_keys
    home = Path.home()
    has_klemmarc = any((home / name).exists() for name in _KLEMMARC_NAMES)
    if has_klemmarc:
        # If user provided an API key, inject it into existing klemmarc
        if values and values.openai_api_key:
            _inject_klemmarc_api_key(home, "openai", values.openai_api_key)
            created.append("~/.klemmarc.yaml (updated api_keys)")
        # If user chose a backend, update it
        if values and values.backend:
            _update_klemmarc_backend(home, values)
            if "~/.klemmarc.yaml (updated api_keys)" not in created:
                created.append("~/.klemmarc.yaml (updated backend)")
        if not created:
            skipped.append("~/.klemmarc.yaml")
    else:
        klemmarc_path = home / ".klemmarc.yaml"
        content = _build_klemmarc(values)
        klemmarc_path.write_text(content, encoding="utf-8")
        klemmarc_path.chmod(0o600)
        created.append("~/.klemmarc.yaml")

    # Legacy ~/.klemma/config.yaml — still created as minimal fallback
    config_target = system_home / "config.yaml"
    if config_target.exists():
        skipped.append("config.yaml")
    else:
        source = _EXAMPLES_DIR / "config.system.example.yaml"
        if source.exists():
            shutil.copy2(source, config_target)
        else:
            config_target.write_text(
                "# Klemma global config — AI defaults\n"
                "ai:\n"
                "  model: sonnet\n"
                "  language: ru\n",
                encoding="utf-8",
            )
        created.append("config.yaml")

    (system_home / "prompts").mkdir(exist_ok=True)

    return {"created": created, "skipped": skipped}


# Legacy alias (kept for migration)
def init_klemma_home(klemma_home: Path) -> dict:
    """Legacy: create klemma_home with template files. Use init_project() instead."""
    return init_system(klemma_home)


def migrate_content_to_klemma_md(project_root: Path) -> dict:
    """Migrate content fields from config.yaml project: section to KLEMMA.md frontmatter.

    Reads: config.yaml project: + dissertation: sections
    Writes: KLEMMA.md frontmatter (preserving existing body)
    Strips: content fields from config.yaml (keeps infrastructure only)

    Returns {"migrated_fields": [...], "warnings": [...]}.
    """
    import yaml as _yaml  # noqa: I001

    from .config import parse_klemma_md, save_klemma_md

    migrated_fields: list[str] = []
    migration_warnings: list[str] = []

    config_path = project_root / ".klemma" / "config.yaml"
    klemma_md_path = project_root / "KLEMMA.md"

    if not config_path.exists():
        migration_warnings.append("config.yaml not found — nothing to migrate")
        return {"migrated_fields": migrated_fields, "warnings": migration_warnings}

    with open(config_path, "r", encoding="utf-8") as f:
        raw = _yaml.safe_load(f) or {}

    # Collect content fields from project: and dissertation: sections
    content_fields = {
        "type", "title", "description", "current_focus",
        "chapters", "scientific_results", "priority_terms",
        "chapter_mapping", "section_type_map", "section_type_weights",
        "deadlines", "writing_constraints", "min_sources_per_section",
        "auto_register", "chapter_plan_pattern", "chapter_draft_pattern",
        "section_weights",
    }

    # Start with dissertation: section (lower priority)
    new_frontmatter: dict = {}
    diss = raw.get("dissertation", {})
    if isinstance(diss, dict):
        # Map dissertation: fields to project fields
        field_map = {
            "title": "title",
            "chapters": "chapters",
            "scientific_results": "scientific_results",
            "priority_terms": "priority_terms",
            "chapter_mapping": "chapter_mapping",
            "section_type_map": "section_type_map",
            "deadlines": "deadlines",
            "writing_constraints": "writing_constraints",
            "min_sources_per_section": "min_sources_per_section",
            "chapter_plan_pattern": "chapter_plan_pattern",
            "chapter_draft_pattern": "chapter_draft_pattern",
            "section_weights": "section_weights",
        }
        for diss_key, proj_key in field_map.items():
            if diss_key in diss:
                new_frontmatter[proj_key] = diss[diss_key]
                migrated_fields.append(f"dissertation.{diss_key}")
        # current_section / current_chapter → current_focus
        if "current_section" in diss:
            new_frontmatter["current_focus"] = diss["current_section"]
            migrated_fields.append("dissertation.current_section")
        elif "current_chapter" in diss:
            new_frontmatter["current_focus"] = str(diss["current_chapter"])
            migrated_fields.append("dissertation.current_chapter")

    # project: section overrides dissertation: (higher priority)
    project = raw.get("project", {})
    if isinstance(project, dict):
        for field in content_fields:
            if field in project:
                new_frontmatter[field] = project[field]
                migrated_fields.append(f"project.{field}")

    if not new_frontmatter:
        migration_warnings.append("No content fields found in config.yaml to migrate")
        return {"migrated_fields": migrated_fields, "warnings": migration_warnings}

    # Ensure chapters keys are int
    if "chapters" in new_frontmatter and isinstance(new_frontmatter["chapters"], dict):
        new_frontmatter["chapters"] = {int(k): v for k, v in new_frontmatter["chapters"].items()}

    # Read existing KLEMMA.md (preserve body, ignore existing frontmatter)
    existing_fm, existing_body = parse_klemma_md(klemma_md_path)
    if existing_fm:
        migration_warnings.append(
            "KLEMMA.md already has frontmatter — merging (new values override old)"
        )
        existing_fm.update(new_frontmatter)
        new_frontmatter = existing_fm
    elif not klemma_md_path.exists():
        existing_body = "# Project Context\n\n<!-- Add project description here. -->\n"

    # Check for Outline_*.md and merge body
    outline_merged = False
    for p in sorted(project_root.glob("Outline_*.md")):
        outline_text = p.read_text(encoding="utf-8")
        # Only merge if KLEMMA.md body doesn't already have ## Outline
        if "## Outline" not in existing_body:
            existing_body = existing_body.rstrip() + f"\n\n## Outline\n\n{outline_text}\n"
            outline_merged = True
            migration_warnings.append(
                f"Merged {p.name} into KLEMMA.md '## Outline' section. "
                "Old file preserved — remove manually."
            )
        break  # only first outline

    if not outline_merged and any(project_root.glob("Outline_*.md")):
        migration_warnings.append(
            "Outline_*.md found but ## Outline already in KLEMMA.md — not merged"
        )

    # Write new KLEMMA.md
    save_klemma_md(klemma_md_path, new_frontmatter, existing_body)

    # Strip content fields from config.yaml (keep infrastructure only)
    infra_keys = {"ai", "zotero", "obsidian", "embeddings", "search", "state",
                  "instance", "tags", "export", "planning", "reading", "processing", "mcp"}
    new_raw = {k: v for k, v in raw.items() if k in infra_keys}
    with open(config_path, "w", encoding="utf-8") as f:
        _yaml.dump(new_raw, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return {"migrated_fields": migrated_fields, "warnings": migration_warnings}
