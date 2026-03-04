"""Configuration loader with Pydantic validation.

Supports Git/NPM-style per-directory projects:
- System config: ~/.klemma/config.yaml (AI defaults)
- Project config: .klemma/config.yaml (per-project settings)
- Context: KLEMMA.md next to .klemma/ (project context for AI)
"""

import logging
import os
import stat
import warnings
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, PrivateAttr

logger = logging.getLogger(__name__)

# Shipped prompts directory (relative to this file → repo root / prompts)
_SHIPPED_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"

# Config keys inherited from parent project (shared resources)
_INHERITED_KEYS = {"obsidian", "zotero", "ai", "embeddings"}

# Claude model shorthands → litellm model IDs (for bare-name detection)
_CLAUDE_SHORTHANDS: dict[str, str] = {
    "opus": "anthropic/claude-opus-4-6",
    "sonnet": "anthropic/claude-sonnet-4-6",
    "haiku": "anthropic/claude-haiku-4-5-20251001",
}

# Models supported by Claude Code CLI (claude -p --model <alias>)
_CLAUDE_CLI_MODELS = {"sonnet", "opus"}

# Known fields per Pydantic model — used by _warn_config_issues()
_KNOWN_FIELDS: dict[str, set[str]] = {}  # populated lazily


def _get_known_fields() -> dict[str, set[str]]:
    """Return known fields for each config section (cached)."""
    if _KNOWN_FIELDS:
        return _KNOWN_FIELDS
    _KNOWN_FIELDS["root"] = set(KlemmaConfig.model_fields.keys())
    _KNOWN_FIELDS["ai"] = set(AIConfig.model_fields.keys())
    _KNOWN_FIELDS["zotero"] = set(ZoteroConfig.model_fields.keys())
    _KNOWN_FIELDS["obsidian"] = set(ObsidianConfig.model_fields.keys())
    _KNOWN_FIELDS["embeddings"] = set(EmbeddingsConfig.model_fields.keys())
    _KNOWN_FIELDS["state"] = set(StateConfig.model_fields.keys())
    _KNOWN_FIELDS["instance"] = set(InstanceConfig.model_fields.keys())
    _KNOWN_FIELDS["project"] = set(ProjectConfig.model_fields.keys())
    _KNOWN_FIELDS["dissertation"] = set(DissertationConfig.model_fields.keys())
    _KNOWN_FIELDS["planning"] = set(PlanningConfig.model_fields.keys())
    _KNOWN_FIELDS["reading"] = set(ReadingConfig.model_fields.keys())
    _KNOWN_FIELDS["processing"] = set(ProcessingConfig.model_fields.keys())
    _KNOWN_FIELDS["tags"] = set(TagsConfig.model_fields.keys())
    _KNOWN_FIELDS["export"] = set(ExportConfig.model_fields.keys())
    return _KNOWN_FIELDS


def _warn_config_issues(raw: dict, source: str) -> None:
    """Emit warnings for common config problems in a raw YAML dict.

    Checks:
    1. Misplaced keys — keys that belong inside a section but sit at root level
    2. Unknown keys — keys not recognized at any level
    3. Bare model names — Claude shorthands used with litellm backend

    Uses warnings.warn() so messages appear on stderr even without logging setup.
    """
    if not raw or not isinstance(raw, dict):
        return

    fields = _get_known_fields()
    root_keys = fields["root"]

    # --- 1. Misplaced keys: known sub-section fields placed at root ---
    # Map: child field → list of sections it belongs to
    child_fields: dict[str, list[str]] = {}
    for section in ("ai", "zotero", "obsidian", "embeddings", "state",
                    "instance", "project", "dissertation", "planning",
                    "reading", "processing", "tags", "export"):
        for field in fields.get(section, set()):
            if field not in root_keys:
                child_fields.setdefault(field, []).append(section)

    for key in raw:
        if key not in root_keys and key in child_fields:
            sections = child_fields[key]
            hint = sections[0] if len(sections) == 1 else "/".join(sections)
            warnings.warn(
                f"[{source}] '{key}' should be inside '{hint}:', not at top level"
                f" (currently ignored — move it under the correct section)",
                UserWarning,
                stacklevel=2,
            )

    # --- 2. Unknown keys at every level ---
    for key in raw:
        if key not in root_keys and key not in child_fields:
            if key in ("api_keys", "mcp"):
                continue  # api_keys valid in klemmarc; mcp reserved
            warnings.warn(
                f"[{source}] unknown top-level key '{key}' (ignored)",
                UserWarning,
                stacklevel=2,
            )

    # Check sub-sections for unknown keys
    for section_name in ("ai", "zotero", "obsidian", "embeddings", "state",
                         "instance", "project", "dissertation", "planning",
                         "reading", "processing", "tags", "export"):
        section_data = raw.get(section_name)
        if not isinstance(section_data, dict):
            continue
        known = fields.get(section_name, set())
        for key in section_data:
            if key not in known:
                warnings.warn(
                    f"[{source}] unknown key '{key}' inside '{section_name}:' (ignored)",
                    UserWarning,
                    stacklevel=2,
                )

    # --- 3. Model compatibility checks ---
    ai_section = raw.get("ai", {})
    if not isinstance(ai_section, dict):
        return
    backend = ai_section.get("backend", "litellm")
    task_classes = ai_section.get("task_classes", {})

    if backend == "claude":
        # Claude CLI: --model requires ANTHROPIC_API_KEY (Max subscriptions don't support it)
        has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        model = ai_section.get("model", "")
        if model and model not in _CLAUDE_CLI_MODELS and "/" not in model:
            warnings.warn(
                f"[{source}] ai.model='{model}' is not supported by Claude CLI."
                f" Supported: {', '.join(sorted(_CLAUDE_CLI_MODELS))}."
                " Use backend: litellm for other models",
                UserWarning,
                stacklevel=2,
            )
        if isinstance(task_classes, dict) and task_classes:
            if not has_api_key:
                warnings.warn(
                    f"[{source}] task_classes requires ANTHROPIC_API_KEY with backend: claude"
                    " (--model flag needs API access). Set ANTHROPIC_API_KEY"
                    " or switch to backend: litellm with class_model_map",
                    UserWarning,
                    stacklevel=2,
                )
            for task, cls in task_classes.items():
                if cls not in _CLAUDE_CLI_MODELS:
                    suggestion = _CLAUDE_SHORTHANDS.get(cls, "")
                    hint = (
                        f" Use backend: litellm with"
                        f" class_model_map.litellm.{cls}: '{suggestion}'"
                        if suggestion else
                        f" Supported by Claude CLI: {', '.join(sorted(_CLAUDE_CLI_MODELS))}"
                    )
                    warnings.warn(
                        f"[{source}] task_classes.{task}='{cls}' is not supported"
                        f" by Claude CLI.{hint}",
                        UserWarning,
                        stacklevel=2,
                    )
    elif backend == "litellm":
        # LiteLLM needs full model IDs, not bare shorthands
        model = ai_section.get("model", "")
        if model in _CLAUDE_SHORTHANDS:
            suggestion = _CLAUDE_SHORTHANDS[model]
            warnings.warn(
                f"[{source}] ai.model='{model}' is a Claude shorthand but backend is litellm."
                f" Use '{suggestion}' or switch to backend: claude",
                UserWarning,
                stacklevel=2,
            )

        class_model_map = ai_section.get("class_model_map", {})
        litellm_map = class_model_map.get("litellm", {}) if isinstance(class_model_map, dict) else {}
        if isinstance(task_classes, dict):
            for task, cls in task_classes.items():
                if cls in _CLAUDE_SHORTHANDS and cls not in litellm_map:
                    suggestion = _CLAUDE_SHORTHANDS[cls]
                    warnings.warn(
                        f"[{source}] task_classes.{task}='{cls}' is a Claude shorthand"
                        f" but backend is litellm and no class_model_map entry exists."
                        f" Add class_model_map.litellm.{cls}: '{suggestion}'",
                        UserWarning,
                        stacklevel=2,
                    )


# --- System home ---


def get_system_home() -> Path:
    """Return klemma system directory (~/.klemma/ or KLEMMA_HOME env var)."""
    return Path(os.environ.get("KLEMMA_HOME", "~/.klemma")).expanduser()


# Backward-compatible alias
get_klemma_home = get_system_home


def ensure_system_home() -> Path:
    """Create ~/.klemma/ with system config if it doesn't exist. Return path.

    Delegates to setup.init_system() for config creation to ensure
    consistent content with 'klemma init --global-only'.
    Also checks ~/.klemmarc.yaml permissions (should be 0o600).
    """
    system_home = get_system_home()
    if not system_home.exists():
        from .setup import init_system
        init_system(system_home)
        logger.info("Created system config at %s", system_home)
    _check_klemmarc_permissions()
    return system_home


def _check_klemmarc_permissions():
    """Fix permissions on ~/.klemmarc* if world-readable (contains secrets)."""
    home = Path.home()
    for name in _KLEMMARC_NAMES:
        path = home / name
        if path.exists():
            mode = path.stat().st_mode
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                path.chmod(0o600)
                logger.warning("Fixed permissions on %s → 0600 (contains secrets)", path)


# --- Project discovery (Git-style) ---


def discover_project_root(start: Optional[Path] = None) -> Optional[Path]:
    """Find nearest directory containing .klemma/ by traversing up from start.

    Returns the directory containing .klemma/, or None if not found.
    Like `git rev-parse --show-toplevel` but for klemma projects.

    Skips the system home directory (~/.klemma/) — that's the global config,
    not a project.
    """
    current = (start or Path.cwd()).resolve()
    system_home = get_system_home().resolve()
    for _ in range(20):  # safety limit
        klemma_dir = current / ".klemma"
        if klemma_dir.is_dir() and klemma_dir.resolve() != system_home:
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def discover_project_chain(start: Optional[Path] = None) -> list[Path]:
    """Find all project roots from current to topmost ancestor.

    Returns list ordered child-first: [child_root, parent_root, grandparent_root].
    Max nesting depth: 3.
    """
    chain: list[Path] = []
    project_root = discover_project_root(start)
    if project_root is None:
        return []
    chain.append(project_root)

    # Look for parent projects
    search_from = project_root.parent
    for _ in range(2):  # max 2 more levels
        parent_project = discover_project_root(search_from)
        if parent_project is None or parent_project == project_root:
            break
        chain.append(parent_project)
        search_from = parent_project.parent
        project_root = parent_project

    return chain


# --- Pydantic config models ---


class ZoteroConfig(BaseModel):
    library_json: Optional[str] = None  # Path to BetterBibTeX JSON export
    storage_path: str = str(Path.home() / "Zotero" / "storage")  # Zotero PDF storage
    collection: Optional[str] = None  # Optional Zotero collection ID for filtering
    library_id: Optional[str] = None  # Zotero library ID (for API access)
    library_type: str = "user"  # "user" or "group"
    api_key_env: str = ""  # env var holding Zotero API key
    local: bool = True  # use local BBT JSON (vs API)


class ObsidianConfig(BaseModel):
    vault_path: str = ""
    notes_folder: str = "2 - Refs"
    tags_folder: str = "3 - Tags"
    use_cli: Optional[bool] = None


class AIConfig(BaseModel):
    backend: str = "litellm"  # "claude" | "litellm" | "openai" (deprecated)
    model: str = "opus"
    max_pdf_chars: int = 50000
    timeout: int = 180
    retries: int = 2
    base_url: Optional[str] = None  # URL for OpenAI-compatible endpoints
    api_key_env: str = ""  # env var name for API key (e.g. "OPENAI_API_KEY")
    json_mode: bool = False  # use structured JSON mode when backend supports it
    language: str = "ru"  # AI response language (e.g. "en", "ru", "de")
    task_classes: dict[str, str] = Field(default_factory=dict)
    # Maps task name → model class: {"planner": "haiku", "research": "opus"}
    class_model_map: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Maps backend → {class → model_id}: {"openai": {"opus": "gpt-4o"}}
    # For claude backend: not needed (class names work as --model shorthands)
    _resolved_api_keys: dict = PrivateAttr(default_factory=dict)

    @property
    def api_key(self) -> Optional[str]:
        """Resolve API key: klemmarc api_keys → env var fallback.

        Looks up the provider derived from backend+model in _resolved_api_keys
        first, then falls back to os.environ.get(api_key_env).
        """
        provider = _derive_provider(self.backend, self.model)
        key = self._resolved_api_keys.get(provider)
        if key:
            return key
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None


class EmbeddingsConfig(BaseModel):
    backend: str = ""  # "s2" | "local" | "openai" | "" (disabled)
    model: str = ""  # model id (backend-specific)
    api_key_env: str = ""  # env var for API key
    base_url: Optional[str] = None  # custom endpoint (OpenAI only)
    throttle: float = 3.1  # seconds between S2 API requests


class StateConfig(BaseModel):
    db_path: str = "./data/klemma.db"
    inherit_db: bool = True  # inherit parent project's DB (read-only)


class ChapterMapping(BaseModel):
    pattern: str
    chapter: int
    section: str


class ChapterDeadline(BaseModel):
    chapter: str  # "1", "2", ..., "introduction", "conclusion"
    deadline: str  # ISO date: "2026-03-15"
    label: str = ""


class DissertationConfig(BaseModel):
    """Legacy dissertation config — kept for migration from old ~/.klemma/ format."""

    current_chapter: int = 2
    current_section: str = "2.3.1"
    title: str = ""
    chapters: dict[int, str] = Field(default_factory=dict)
    scientific_results: dict[str, str] = Field(default_factory=dict)
    priority_terms: list[str] = Field(default_factory=list)
    chapter_mapping: list[ChapterMapping] = Field(default_factory=list)
    min_sources_per_section: int = 3
    deadlines: list[ChapterDeadline] = Field(default_factory=list)
    chapter_plan_pattern: str = "План_Глава{chapter}"
    writing_constraints: str = "1-1.5 ч/день, 200-300 слов/день, Pomodoro"
    chapter_draft_pattern: str = "Глава_{chapter}"
    section_weights: dict[str, float] = Field(default_factory=dict)
    section_type_map: dict[str, str] = Field(default_factory=dict)


class ProjectConfig(BaseModel):
    """Configuration for a single academic work (dissertation, paper, thesis)."""

    type: str = "dissertation"  # dissertation | paper | thesis
    title: str = ""
    description: str = ""  # 1-2 sentence research description (used for source discovery)
    current_focus: str = ""  # e.g. "2.3.1" for dissertation, "methods" for paper
    chapters: dict[int, str] = Field(default_factory=dict)
    scientific_results: dict[str, str] = Field(default_factory=dict)
    priority_terms: list[str] = Field(default_factory=list)
    chapter_mapping: list[ChapterMapping] = Field(default_factory=list)
    min_sources_per_section: int = 3
    deadlines: list[ChapterDeadline] = Field(default_factory=list)
    chapter_plan_pattern: str = "План_Глава{chapter}"
    writing_constraints: str = ""
    chapter_draft_pattern: str = "Глава_{chapter}"
    section_weights: dict[str, float] = Field(default_factory=dict)
    section_type_map: dict[str, str] = Field(default_factory=dict)
    # Maps numeric section → semantic type: {"2": "literature_review", "3": "methodology"}
    section_type_weights: dict[str, float] = Field(default_factory=dict)
    # Optional weights by semantic type for gap scoring: {"methodology": 1.0, "appendix": 0.3}

    @property
    def current_chapter(self) -> int:
        """Extract chapter number from current_focus (e.g. '2.3.1' -> 2)."""
        if not self.current_focus:
            return 1
        first = self.current_focus.split(".")[0]
        try:
            return int(first)
        except ValueError:
            return 1

    @property
    def current_section(self) -> str:
        """Return current_focus as section identifier."""
        return self.current_focus

    @property
    def chapter_numbers(self) -> list[int]:
        """All chapter numbers, sorted."""
        return sorted(self.chapters.keys()) if self.chapters else [1]

    @classmethod
    def from_dissertation(cls, d: DissertationConfig) -> "ProjectConfig":
        """Convert legacy DissertationConfig to ProjectConfig."""
        # Use explicit map if provided, otherwise auto-infer from chapter names
        section_type_map: dict[str, str] = dict(d.section_type_map)
        if not section_type_map and d.chapters:
            from .section_types import infer_section_type
            for ch_num, ch_name in d.chapters.items():
                inferred = infer_section_type(ch_name)
                if inferred:
                    section_type_map[str(ch_num)] = inferred.value

        return cls(
            type="dissertation",
            title=d.title,
            current_focus=d.current_section or str(d.current_chapter),
            chapters=d.chapters,
            scientific_results=d.scientific_results,
            priority_terms=d.priority_terms,
            chapter_mapping=d.chapter_mapping,
            min_sources_per_section=d.min_sources_per_section,
            deadlines=d.deadlines,
            chapter_plan_pattern=d.chapter_plan_pattern,
            writing_constraints=d.writing_constraints,
            chapter_draft_pattern=d.chapter_draft_pattern,
            section_type_map=section_type_map,
        )


def parse_chapter_from_section(section: str) -> Optional[int]:
    """Extract chapter number from 'X.Y.Z' section string.

    Returns None for topic-based sections (e.g. 'methods', 'introduction').
    """
    try:
        return int(section.split(".")[0])
    except (ValueError, IndexError):
        return None


class PlanningConfig(BaseModel):
    dissertation_focus: str = ""
    assistant_roadmap: list[str] = Field(default_factory=list)


class ReadingConfig(BaseModel):
    snippet_length: int = 2000
    daily_papers: int = 1
    priority_boost_chapters: list[int] = Field(default_factory=list)


class ProcessingConfig(BaseModel):
    batch_size: int = 2
    skip_no_pdf: bool = True
    min_pdf_length: int = 500


class TagMapping(BaseModel):
    pattern: str
    tag: str


class TagsConfig(BaseModel):
    auto_mapping: list[TagMapping] = Field(default_factory=list)


class ExportPandocConfig(BaseModel):
    timeout: int = 300
    number_sections: bool = True


class ExportConfig(BaseModel):
    output_dir: str = "./exports"
    pandoc: ExportPandocConfig = Field(default_factory=ExportPandocConfig)


class InstanceConfig(BaseModel):
    name: str = "default"
    type: str = "academic"
    dev_mode: bool = False  # enables benchmark candidate hints after acquire/process


class SystemConfig(BaseModel):
    """Global system configuration (~/.klemma/config.yaml).

    Contains defaults that apply across all projects unless overridden.
    """

    ai: AIConfig = Field(default_factory=AIConfig)


class KlemmaConfig(BaseModel):
    instance: InstanceConfig = Field(default_factory=InstanceConfig)
    zotero: ZoteroConfig = Field(default_factory=ZoteroConfig)
    obsidian: ObsidianConfig = Field(default_factory=ObsidianConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    dissertation: DissertationConfig = Field(default_factory=DissertationConfig)
    planning: PlanningConfig = Field(default_factory=PlanningConfig)
    reading: ReadingConfig = Field(default_factory=ReadingConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    tags: TagsConfig = Field(default_factory=TagsConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    project: Optional[ProjectConfig] = None


# --- Config loading and merging ---


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base dict. Override values win."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml(path: Path) -> dict:
    """Load a YAML file, returning empty dict if missing or empty."""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# --- klemmarc global config ---

_KLEMMARC_NAMES = (".klemmarc.yaml", ".klemmarc.yml", ".klemmarc")


def _load_klemmarc() -> dict:
    """Load ~/.klemmarc.yaml (or .yml / .klemmarc) global config.

    Returns raw dict (including api_keys section). Empty dict if missing.
    """
    home = Path.home()
    for name in _KLEMMARC_NAMES:
        path = home / name
        if path.exists():
            return _load_yaml(path)
    return {}


def _derive_provider(backend: str, model: str) -> str:
    """Extract provider name from backend+model for api_keys lookup.

    Examples:
        litellm + "anthropic/claude-sonnet" → "anthropic"
        litellm + "gpt-4.1" (bare) → "openai"
        "openai" backend → "openai"
        "claude" backend → "anthropic"
    """
    if backend == "claude":
        return "anthropic"
    if backend == "openai":
        return "openai"
    # litellm: model may be "provider/model-name" or bare
    if "/" in model:
        return model.split("/", 1)[0]
    # Bare model name → assume openai
    return "openai"


def load_config(config_path: str | Path | None = None) -> KlemmaConfig:
    """Load and validate configuration from YAML file.

    If config_path is None, uses get_system_home() / "config.yaml".
    Supports both legacy single-project configs and new project-aware configs.
    """
    if config_path is None:
        path = get_system_home() / "config.yaml"
    else:
        path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw = _load_yaml(path)
    if raw:
        _warn_config_issues(raw, str(path))

    # Migration: Zotero fields were originally under instance:
    if "zotero" not in raw and "instance" in raw:
        inst = raw["instance"]
        zotero_fields = {"library_json", "storage_path", "collection"}
        migrated = {k: v for k, v in inst.items() if k in zotero_fields}
        if migrated:
            raw["zotero"] = migrated

    return KlemmaConfig.model_validate(raw)


def resolve_effective_config(
    project_chain: list[Path],
    config_override: Optional[str | Path] = None,
) -> tuple[KlemmaConfig, ProjectConfig, Path]:
    """Resolve effective config by merging: klemmarc → system → parent → child → CLI.

    project_chain is child-first: [child_root, parent_root].
    Can be empty if only config_override is given (fallback to override path).

    Merge order (later wins):
    ~/.klemmarc.yaml < ~/.klemma/config.yaml < parent project < child project < CLI override

    Selective inheritance: only shared resource keys (obsidian, zotero, ai)
    are inherited from parent. Project structure (project, tags, state, etc.)
    is always per-project.

    Returns (merged_config, project_config, active_project_root).
    """
    # 0. klemmarc base layer (global config with api_keys)
    klemmarc_raw = _load_klemmarc()
    api_keys = klemmarc_raw.pop("api_keys", {})
    # Start from klemmarc (without api_keys — not part of KlemmaConfig schema)
    if klemmarc_raw:
        _warn_config_issues(klemmarc_raw, "~/.klemmarc.yaml")

    # 1. System defaults
    system_path = get_system_home() / "config.yaml"
    system_raw = _load_yaml(system_path)
    if system_raw:
        _warn_config_issues(system_raw, str(system_path))

    # 2. Project chain: parent first, child last (child wins)
    #    Only inherit shared resource keys from parents
    merged: dict[str, Any] = {}
    for root in reversed(project_chain):  # parent first
        config_file = root / ".klemma" / "config.yaml"
        project_raw = _load_yaml(config_file)
        if project_raw:
            _warn_config_issues(project_raw, str(config_file))
        if root == project_chain[0]:
            # Active (child) project: merge everything
            merged = _deep_merge(merged, project_raw)
        else:
            # Parent project: only inherit shared resource keys
            inherited = {k: v for k, v in project_raw.items() if k in _INHERITED_KEYS}
            merged = _deep_merge(merged, inherited)

    # 3. Merge: klemmarc < system < projects
    effective = _deep_merge(klemmarc_raw, system_raw)
    effective = _deep_merge(effective, merged)

    # 4. CLI --config override wins over everything
    if config_override:
        override_path = Path(config_override)
        override_raw = _load_yaml(override_path)
        if override_raw:
            _warn_config_issues(override_raw, str(override_path))
        effective = _deep_merge(effective, override_raw)

    cfg = KlemmaConfig.model_validate(effective)

    # Inject resolved api_keys (PrivateAttr, never serialized)
    if api_keys:
        cfg.ai._resolved_api_keys = api_keys

    # Determine project root
    if project_chain:
        project_root = project_chain[0]
    elif config_override:
        # No project discovered — derive root from config file location
        project_root = Path(config_override).resolve().parent
        if project_root.name == ".klemma":
            project_root = project_root.parent
    else:
        project_root = Path.cwd()

    # Extract ProjectConfig
    if cfg.project:
        project = cfg.project
    elif cfg.dissertation and cfg.dissertation.title:
        project = ProjectConfig.from_dissertation(cfg.dissertation)
    else:
        project = ProjectConfig()

    return cfg, project, project_root


# --- Context and tags ---


def load_project_context(project_chain: list[Path], config: Optional[KlemmaConfig] = None) -> str:
    """Load and aggregate KLEMMA.md files from project chain.

    project_chain is child-first. Result: parent context first, then child.
    Falls back to .klemma/context.md (legacy) and then config fields.
    """
    contexts: list[str] = []

    for root in reversed(project_chain):  # parent first
        # Try KLEMMA.md first
        klemma_md = root / "KLEMMA.md"
        if klemma_md.exists():
            text = klemma_md.read_text(encoding="utf-8").strip()
            if text:
                contexts.append(text)
                continue

        # Legacy fallback: .klemma/context.md
        context_md = root / ".klemma" / "context.md"
        if context_md.exists():
            text = context_md.read_text(encoding="utf-8").strip()
            if text:
                contexts.append(text)

    if contexts:
        return "\n\n---\n\n".join(contexts)

    # Final fallback: build from config fields
    if config:
        return _build_context_from_config(config)
    return ""


def _build_context_from_config(config: KlemmaConfig) -> str:
    """Build context string from config.dissertation or config.project fields."""
    # Try project config first
    if config.project and config.project.title:
        p = config.project
        parts = [f"Topic: {p.title}"]
        if p.scientific_results:
            parts.append("")
            for key, val in p.scientific_results.items():
                parts.append(f"{key.upper()}: {val}")
        if p.chapters:
            parts.append("")
            parts.append("Chapters:")
            for ch_num, ch_name in sorted(p.chapters.items()):
                parts.append(f"{ch_num}. {ch_name}")
        if p.priority_terms:
            parts.append("")
            parts.append(f"Key terms: {', '.join(p.priority_terms)}")
        return "\n".join(parts)

    # Legacy dissertation config fallback
    d = config.dissertation
    parts: list[str] = []
    if d.title:
        parts.append(f"Topic: {d.title}")
    if d.scientific_results:
        parts.append("")
        for key, val in d.scientific_results.items():
            parts.append(f"{key.upper()}: {val}")
    if d.chapters:
        parts.append("")
        parts.append("Chapters:")
        for ch_num, ch_name in sorted(d.chapters.items()):
            parts.append(f"{ch_num}. {ch_name}")
    if d.priority_terms:
        parts.append("")
        parts.append(f"Key terms: {', '.join(d.priority_terms)}")
    return "\n".join(parts)


# Keep old name as alias for backward compatibility
load_dissertation_context = load_project_context


def load_available_tags(
    klemma_home: Path,
    config: KlemmaConfig,
    project_chain: Optional[list[Path]] = None,
) -> list[str]:
    """Load available tags with fallback through project chain.

    Resolution order: project → parent projects → config.tags.auto_mapping.
    """
    # 1. Active project
    tags_path = klemma_home / "tags.yaml"
    if tags_path.exists():
        with open(tags_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, list):
            return data

    # 2. Parent projects in chain
    if project_chain:
        for root in project_chain[1:]:  # skip child (already checked above)
            parent_tags = root / ".klemma" / "tags.yaml"
            if parent_tags.exists():
                with open(parent_tags, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, list):
                    return data

    # 3. Fallback: extract from auto_mapping
    seen: set[str] = set()
    tags: list[str] = []
    for mapping in config.tags.auto_mapping:
        if mapping.tag not in seen:
            tags.append(mapping.tag)
            seen.add(mapping.tag)
    return tags


def resolve_prompt(
    name: str,
    klemma_home: Path,
    project_chain: Optional[list[Path]] = None,
) -> Path:
    """Resolve prompt template path with 4-level lookup.

    Priority: project → parent project → system (~/.klemma/) → shipped.
    klemma_home is the active project's .klemma/ directory.
    If project_chain is given, uses it directly instead of re-discovering.
    """
    # 1. Active project
    user_path = klemma_home / "prompts" / name
    if user_path.exists():
        return user_path

    # 2. Parent projects
    if project_chain is not None:
        # Use known chain (skip child at index 0 — already checked above)
        for root in project_chain[1:]:
            parent_path = root / ".klemma" / "prompts" / name
            if parent_path.exists():
                return parent_path
    else:
        # Fallback: traverse up from project root
        project_root = klemma_home.parent
        search_from = project_root.parent
        for _ in range(2):
            parent_root = discover_project_root(search_from)
            if parent_root is None or parent_root == project_root:
                break
            parent_path = parent_root / ".klemma" / "prompts" / name
            if parent_path.exists():
                return parent_path
            search_from = parent_root.parent

    # 3. System-level override (~/.klemma/prompts/)
    system_path = get_system_home() / "prompts" / name
    if system_path.exists():
        return system_path

    # 4. Shipped prompts
    return _SHIPPED_PROMPTS_DIR / name


# --- Project file scanning ---

_SCAN_EXTENSIONS = {".md", ".tex", ".bib", ".txt", ".rst"}
_SCAN_EXCLUDE_DIRS = {".klemma", ".git", "__pycache__", ".venv", "node_modules", ".claude"}


def scan_project_files(
    project_root: Path, max_chars_per_file: int = 3000,
) -> list[dict]:
    """Scan project directory for text files useful as AI context.

    Returns list of dicts: {name, path, size, content_preview}.
    Excludes internal dirs (.klemma/, .git/, etc.).
    """
    results = []
    for p in sorted(project_root.rglob("*")):
        if not p.is_file() or p.suffix not in _SCAN_EXTENSIONS:
            continue
        # Skip excluded directories
        if any(part in _SCAN_EXCLUDE_DIRS for part in p.relative_to(project_root).parts):
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        results.append({
            "name": p.name,
            "path": str(p.relative_to(project_root)),
            "size": p.stat().st_size,
            "content_preview": content[:max_chars_per_file],
        })
    return results


def update_project_config(project_root: Path, updates: dict) -> None:
    """Merge updates into .klemma/config.yaml (project section only).

    Only updates keys present in `updates`. Preserves other config.
    """
    config_path = project_root / ".klemma" / "config.yaml"
    if not config_path.exists():
        logger.warning("Config not found: %s", config_path)
        return

    raw = _load_yaml(config_path)
    project_section = raw.get("project", {})
    project_section.update(updates)
    raw["project"] = project_section

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
