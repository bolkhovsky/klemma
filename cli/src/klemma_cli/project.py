"""Project discovery — find .klemma/ directory and parse KLEMMA.md."""

from __future__ import annotations

from pathlib import Path


def discover_project_root(start: Path | None = None) -> Path | None:
    """Traverse up from start (or cwd) to find nearest .klemma/ directory.

    Returns the directory containing .klemma/, or None if not found.
    Stops at filesystem root to avoid infinite loop.
    """
    current = (start or Path.cwd()).resolve()
    while True:
        if (current / ".klemma").is_dir():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def ensure_project_root(start: Path | None = None) -> Path:
    """Like discover_project_root, but raises if not found."""
    root = discover_project_root(start)
    if root is None:
        raise FileNotFoundError(
            "No .klemma/ directory found. Run 'klemma init' first or cd into a klemma project."
        )
    return root


def get_project_name(project_root: Path) -> str:
    """Extract project name from KLEMMA.md frontmatter or directory name."""
    klemma_md = project_root / "KLEMMA.md"
    if klemma_md.exists():
        text = klemma_md.read_text(errors="replace")
        if text.startswith("---"):
            lines = text.split("\n")
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                if line.startswith("name:"):
                    return line.split(":", 1)[1].strip().strip("'\"")
    return project_root.name
