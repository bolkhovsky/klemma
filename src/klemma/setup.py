"""Setup logic for `klemma init` — creates ~/.klemma/ with template files."""

import shutil
from pathlib import Path


# Example files shipped with the package (repo root)
_EXAMPLES_DIR = Path(__file__).parent.parent.parent


def init_klemma_home(klemma_home: Path) -> dict:
    """Create klemma_home directory with template files.

    Returns dict with keys: created (list of file names), skipped (list of file names).
    """
    created: list[str] = []
    skipped: list[str] = []

    klemma_home.mkdir(parents=True, exist_ok=True)
    (klemma_home / "data").mkdir(exist_ok=True)

    templates = {
        "config.yaml": _EXAMPLES_DIR / "config.example.yaml",
        "context.md": _EXAMPLES_DIR / "context.example.md",
        "tags.yaml": _EXAMPLES_DIR / "tags.example.yaml",
    }

    for target_name, source_path in templates.items():
        target = klemma_home / target_name
        if target.exists():
            skipped.append(target_name)
            continue

        if source_path.exists():
            shutil.copy2(source_path, target)
        else:
            # Create minimal placeholder if example file not found
            target.write_text(f"# {target_name} — edit this file\n", encoding="utf-8")
        created.append(target_name)

    return {"created": created, "skipped": skipped}
