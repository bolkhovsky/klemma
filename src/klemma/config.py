"""Configuration loader with Pydantic validation."""

import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Shipped prompts directory (relative to this file → repo root / prompts)
_SHIPPED_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


def get_klemma_home() -> Path:
    """Return klemma home directory (~/.klemma/ or KLEMMA_HOME env var)."""
    return Path(os.environ.get("KLEMMA_HOME", "~/.klemma")).expanduser()


class ZoteroConfig(BaseModel):
    library_id: str = ""
    library_type: str = "user"
    api_key_env: str = "ZOTERO_API_KEY"
    local: bool = False
    library_json: Optional[str] = None  # Path to BetterBibTeX JSON export
    storage_path: str = str(Path.home() / "Zotero" / "storage")  # Zotero PDF storage
    backend: str = "local"  # "local" (BBT JSON) | "mcp" (zotero-mcp server)
    collection: Optional[str] = None  # Optional Zotero collection ID for filtering

    @property
    def api_key(self) -> Optional[str]:
        return os.environ.get(self.api_key_env)


class ObsidianConfig(BaseModel):
    vault_path: str
    notes_folder: str = "2 - Refs"
    tags_folder: str = "3 - Tags"
    use_cli: Optional[bool] = None


class AIConfig(BaseModel):
    backend: str = "claude"  # "claude" | "openai" | "litellm"
    model: str = "opus"
    max_pdf_chars: int = 50000
    timeout: int = 180
    retries: int = 2
    base_url: Optional[str] = None  # URL for OpenAI-compatible endpoints
    api_key_env: str = ""  # env var name for API key (e.g. "OPENAI_API_KEY")
    json_mode: bool = False  # use structured JSON mode when backend supports it
    language: str = "ru"  # AI response language (e.g. "en", "ru", "de")

    @property
    def api_key(self) -> Optional[str]:
        """Resolve API key from environment variable."""
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None


class StateConfig(BaseModel):
    db_path: str = "./data/klemma.db"


class ChapterMapping(BaseModel):
    pattern: str
    chapter: int
    section: str


class ChapterDeadline(BaseModel):
    chapter: str  # "1", "2", ..., "introduction", "conclusion"
    deadline: str  # ISO date: "2026-03-15"
    label: str = ""


class DissertationConfig(BaseModel):
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


# --- Multi-project support ---


class ProjectConfig(BaseModel):
    """Configuration for a single academic work (dissertation, paper, thesis)."""

    type: str = "dissertation"  # dissertation | paper | thesis
    title: str = ""
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
        )


class WorkspaceConfig(BaseModel):
    """Workspace configuration — maps project names to config file paths."""

    active: str = "default"
    defaults: dict[str, Any] = Field(default_factory=dict)
    projects: dict[str, str] = Field(default_factory=dict)  # name → config path


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


class MCPServerConfig(BaseModel):
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class MCPConfig(BaseModel):
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class KlemmaConfig(BaseModel):
    instance: InstanceConfig = Field(default_factory=InstanceConfig)
    zotero: ZoteroConfig = Field(default_factory=ZoteroConfig)
    obsidian: ObsidianConfig
    ai: AIConfig = Field(default_factory=AIConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    dissertation: DissertationConfig = Field(default_factory=DissertationConfig)
    planning: PlanningConfig = Field(default_factory=PlanningConfig)
    reading: ReadingConfig = Field(default_factory=ReadingConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    tags: TagsConfig = Field(default_factory=TagsConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    # Multi-project: optional project config embedded in same file
    project: Optional[ProjectConfig] = None


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base dict. Override values win."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: str | Path | None = None) -> KlemmaConfig:
    """Load and validate configuration from YAML file.

    If config_path is None, uses get_klemma_home() / "config.yaml".
    Supports both legacy single-project configs and new project-aware configs.
    """
    if config_path is None:
        path = get_klemma_home() / "config.yaml"
    else:
        path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # Migration: Zotero fields were originally under instance:
    if "zotero" not in raw and "instance" in raw:
        inst = raw["instance"]
        zotero_fields = {"library_id", "library_type", "api_key_env", "local", "library_json"}
        migrated = {k: v for k, v in inst.items() if k in zotero_fields}
        if migrated:
            raw["zotero"] = migrated

    return KlemmaConfig.model_validate(raw)


def load_workspace(
    workspace_path: str | Path = "workspace.yaml",
    project_name: Optional[str] = None,
) -> tuple[KlemmaConfig, ProjectConfig, str]:
    """Load workspace and resolve a project config.

    Returns (config, project, project_name).

    The workspace.yaml points to per-project config files.
    Shared defaults from workspace are merged under each project config.
    """
    ws_path = Path(workspace_path)
    if not ws_path.exists():
        raise FileNotFoundError(f"Workspace file not found: {ws_path}")

    with open(ws_path, "r", encoding="utf-8") as f:
        ws_raw = yaml.safe_load(f) or {}

    ws = WorkspaceConfig.model_validate(ws_raw)
    name = project_name or ws.active

    if name not in ws.projects:
        available = ", ".join(ws.projects.keys()) or "(none)"
        raise ValueError(f"Project '{name}' not found in workspace. Available: {available}")

    # Resolve project config path (relative to workspace file)
    project_config_path = ws_path.parent / ws.projects[name]
    if not project_config_path.exists():
        raise FileNotFoundError(
            f"Project config not found: {project_config_path} (for project '{name}')"
        )

    with open(project_config_path, "r", encoding="utf-8") as f:
        proj_raw = yaml.safe_load(f) or {}

    # Merge workspace defaults under project config (project values win)
    merged = _deep_merge(ws.defaults, proj_raw)

    # Load the merged config as KlemmaConfig
    cfg = KlemmaConfig.model_validate(merged)

    # Extract ProjectConfig: prefer explicit 'project:' block, else synthesize from 'dissertation:'
    if "project" in merged and merged["project"]:
        project = ProjectConfig.model_validate(merged["project"])
    else:
        project = ProjectConfig.from_dissertation(cfg.dissertation)

    return cfg, project, name


def resolve_project(
    config_path: str | Path | None = None,
    workspace_path: Optional[str | Path] = None,
    project_name: Optional[str] = None,
) -> tuple[KlemmaConfig, ProjectConfig, str]:
    """Unified entry point: resolve config + project from either workspace or direct config.

    Priority:
    1. If workspace_path is given and exists, use workspace mode
    2. If workspace.yaml exists in cwd, use workspace mode
    3. Fall back to direct config mode (legacy)
    """
    # Try workspace mode
    ws_path = Path(workspace_path) if workspace_path else Path("workspace.yaml")
    if ws_path.exists():
        return load_workspace(ws_path, project_name=project_name)

    # Direct config mode (legacy or new-style single project)
    cfg = load_config(config_path)

    # If config has explicit project: block, use it
    if cfg.project:
        name = project_name or "default"
        return cfg, cfg.project, name

    # Legacy: synthesize ProjectConfig from DissertationConfig
    project = ProjectConfig.from_dissertation(cfg.dissertation)
    name = project_name or "default"
    return cfg, project, name


def load_dissertation_context(klemma_home: Path, config: KlemmaConfig) -> str:
    """Load dissertation context from context.md or build from config fields.

    Looks for klemma_home/context.md first. If not found, builds a basic
    context string from config.dissertation fields as fallback.
    """
    context_path = klemma_home / "context.md"
    if context_path.exists():
        text = context_path.read_text(encoding="utf-8").strip()
        if text:
            return text

    # Fallback: build from config fields
    parts = []
    if config.dissertation.title:
        parts.append(f"Topic: {config.dissertation.title}")
    if config.dissertation.scientific_results:
        parts.append("")
        for key, val in config.dissertation.scientific_results.items():
            parts.append(f"{key.upper()}: {val}")
    if config.dissertation.chapters:
        parts.append("")
        parts.append("Chapters:")
        for ch_num, ch_name in sorted(config.dissertation.chapters.items()):
            parts.append(f"{ch_num}. {ch_name}")
    if config.dissertation.priority_terms:
        parts.append("")
        parts.append(f"Key terms: {', '.join(config.dissertation.priority_terms)}")

    return "\n".join(parts)


def load_available_tags(klemma_home: Path, config: KlemmaConfig) -> list[str]:
    """Load available tags from tags.yaml or extract from config.

    Looks for klemma_home/tags.yaml first. If not found, extracts unique
    tag names from config.tags.auto_mapping as fallback.
    """
    tags_path = klemma_home / "tags.yaml"
    if tags_path.exists():
        with open(tags_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, list):
            return data

    # Fallback: extract from auto_mapping
    seen: set[str] = set()
    tags: list[str] = []
    for mapping in config.tags.auto_mapping:
        if mapping.tag not in seen:
            tags.append(mapping.tag)
            seen.add(mapping.tag)
    return tags


def resolve_prompt(name: str, klemma_home: Path) -> Path:
    """Resolve prompt template path: user override first, then shipped.

    Looks in klemma_home/prompts/ first, then the shipped prompts/ directory.
    """
    user_path = klemma_home / "prompts" / name
    if user_path.exists():
        return user_path
    return _SHIPPED_PROMPTS_DIR / name
