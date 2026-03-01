"""Setup logic for `klemma init` — creates .klemma/ project in current directory.

Two modes:
- init_project(): creates .klemma/ + KLEMMA.md in a project directory
- init_system(): creates ~/.klemma/ with minimal global config
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
    notes_folder: str = "References"
    tags_folder: str = "Tags"
    zotero_storage: str = ""
    zotero_library_json: str = ""
    def __post_init__(self):
        if self.keywords is None:
            self.keywords = []


def _build_project_config(values: InitValues) -> dict:
    """Build config dict from wizard values."""
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

    # AI
    cfg["ai"] = {"model": "sonnet", "language": values.language}

    # Project
    project: dict = {"type": values.project_type}
    if values.title:
        project["title"] = values.title
    if values.description:
        project["description"] = values.description
    if values.keywords:
        project["priority_terms"] = values.keywords
    cfg["project"] = project

    # State
    cfg["state"] = {"db_path": "./data/klemma.db"}

    return cfg


def _build_klemma_md(values: InitValues) -> str:
    """Build KLEMMA.md content from wizard values."""
    title = values.title or "Your project title here"
    lines = [
        "# Project Context\n",
        "<!-- This file describes your project for AI. It is passed as context to all",
        "     AI-powered commands (plan, research, process, ask, etc.).",
        "",
        "     For nested projects, context is aggregated: parent context first, then child.",
        "     For example, if this is a paper inside a dissertation directory, AI will see",
        "     the dissertation context followed by this paper's context. -->\n",
        f'Topic: "{title}"\n',
    ]

    if values.description:
        lines.append(f"Description: {values.description}\n")

    lines.append("Scientific Results:")
    lines.append("- NR1: First scientific result")
    lines.append("- NR2: Second scientific result\n")

    if values.keywords:
        lines.append(f"Key terms: {', '.join(values.keywords)}")
    else:
        lines.append("Key terms: term1, term2, term3")
    lines.append("")

    return "\n".join(lines)


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
) -> dict:
    """Create .klemma/ project in project_dir + KLEMMA.md.

    If values is provided (interactive mode), writes config from discovered/prompted
    values. Otherwise falls back to example template (--no-input / legacy).

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
            cfg = _build_project_config(values)
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
"""

_KLEMMARC_NAMES = (".klemmarc.yaml", ".klemmarc.yml", ".klemmarc")


def init_system(system_home: Path) -> dict:
    """Create ~/.klemma/ system directory with global config + ~/.klemmarc.yaml.

    Returns dict with keys: created, skipped.
    """
    created: list[str] = []
    skipped: list[str] = []

    system_home.mkdir(parents=True, exist_ok=True)

    # ~/.klemmarc.yaml — new global config with api_keys
    home = Path.home()
    has_klemmarc = any((home / name).exists() for name in _KLEMMARC_NAMES)
    if has_klemmarc:
        skipped.append("~/.klemmarc.yaml")
    else:
        klemmarc_path = home / ".klemmarc.yaml"
        klemmarc_path.write_text(_KLEMMARC_TEMPLATE, encoding="utf-8")
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
