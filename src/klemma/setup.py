"""Setup logic for `klemma init` — creates .klemma/ project in current directory.

Two modes:
- init_project(): creates .klemma/ + KLEMMA.md in a project directory
- init_system(): creates ~/.klemma/ with minimal global config
"""

import shutil
from pathlib import Path

# Example files shipped with the package (repo root)
_EXAMPLES_DIR = Path(__file__).parent.parent.parent


def init_project(project_dir: Path, project_type: str = "dissertation") -> dict:
    """Create .klemma/ project in project_dir + KLEMMA.md.

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

    # .gitignore: exclude DB but keep config in VCS
    gitignore = project_dir / ".gitignore"
    ignore_line = ".klemma/data/"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if ignore_line not in content:
            with open(gitignore, "a", encoding="utf-8") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(f"{ignore_line}\n")
            created.append(".gitignore (updated)")
    else:
        gitignore.write_text(f"# Klemma data (SQLite DB)\n{ignore_line}\n", encoding="utf-8")
        created.append(".gitignore")

    return {"created": created, "skipped": skipped}


def init_system(system_home: Path) -> dict:
    """Create ~/.klemma/ system directory with global config.

    Returns dict with keys: created, skipped.
    """
    created: list[str] = []
    skipped: list[str] = []

    system_home.mkdir(parents=True, exist_ok=True)

    config_target = system_home / "config.yaml"
    if config_target.exists():
        skipped.append("config.yaml")
    else:
        source = _EXAMPLES_DIR / "config.system.example.yaml"
        if source.exists():
            shutil.copy2(source, config_target)
        else:
            config_target.write_text(
                "# Klemma global config — AI defaults, shared MCP servers\n"
                "ai:\n"
                "  model: sonnet\n"
                "  language: ru\n"
                "\n"
                "mcp:\n"
                "  servers: {}\n",
                encoding="utf-8",
            )
        created.append("config.yaml")

    (system_home / "prompts").mkdir(exist_ok=True)

    return {"created": created, "skipped": skipped}


# Legacy alias (kept for migration)
def init_klemma_home(klemma_home: Path) -> dict:
    """Legacy: create klemma_home with template files. Use init_project() instead."""
    return init_system(klemma_home)
