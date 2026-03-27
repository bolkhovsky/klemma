#!/usr/bin/env python3
"""Migrate notes/drafts/*.md → draft/chapter_N.md (ADR-016).

Usage:
    python scripts/migrate_drafts.py [project_root]

If project_root is omitted, uses the current directory.

Strategy:
  - Scan notes/drafts/*.md
  - Group files by chapter number detected from filename
    Draft_1.md, Draft_1.1.md, Draft_1.1_newest.md, Draft_1.2_v2.md → chapter 1
    Draft_2.md, Draft_2.1.md → chapter 2
    intro.md, введение.md → intro.md
    conclusion.md, заключение.md → conclusion.md
  - Within each chapter, pick the largest file (most content = canonical version)
  - Write merged output to draft/chapter_N.md (or draft/intro.md, draft/conclusion.md)
  - Leave originals in notes/drafts/ untouched
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _detect_chapter(filename: str) -> str:
    """Return canonical file id for a notes/drafts filename.

    Examples:
      Draft_1.md         → chapter_1
      Draft_1.1.md       → chapter_1
      Draft_1.1_newest   → chapter_1
      Draft_2.3_v2.md    → chapter_2
      intro.md           → intro
      введение.md        → intro
      conclusion.md      → conclusion
      заключение.md      → conclusion
    """
    stem = Path(filename).stem.lower()

    # Unnumbered special sections
    if re.search(r"(intro|введени)", stem):
        return "intro"
    if re.search(r"(conclusion|заключени|выводы)", stem):
        return "conclusion"

    # Draft_N or Draft_N.M... → chapter N
    m = re.search(r"draft[_\s]*(\d+)", stem)
    if m:
        return f"chapter_{m.group(1)}"

    # Bare numeric: 1.md, 1.1.md, 2_3.md
    m = re.match(r"^(\d+)", stem)
    if m:
        return f"chapter_{m.group(1)}"

    return stem  # unknown — use stem as-is


def migrate(project_root: Path) -> None:
    source_dir = project_root / "notes" / "drafts"
    target_dir = project_root / "draft"

    if not source_dir.exists():
        print(f"No notes/drafts/ directory found in {project_root}")
        return

    md_files = list(source_dir.glob("*.md"))
    if not md_files:
        print("notes/drafts/ is empty — nothing to migrate.")
        return

    # Group by chapter id, tracking largest file per group
    groups: dict[str, list[Path]] = {}
    for f in md_files:
        chapter_id = _detect_chapter(f.name)
        groups.setdefault(chapter_id, []).append(f)

    target_dir.mkdir(exist_ok=True)
    print(f"Migrating {len(md_files)} file(s) from {source_dir} → {target_dir}\n")

    for chapter_id, files in sorted(groups.items()):
        # Pick largest file as canonical
        canonical = max(files, key=lambda f: f.stat().st_size)
        target = target_dir / f"{chapter_id}.md"

        if target.exists():
            print(f"  SKIP  {chapter_id}.md (already exists — delete manually to re-migrate)")
            continue

        content = canonical.read_text(encoding="utf-8")
        target.write_text(content, encoding="utf-8")

        skipped = [f.name for f in files if f != canonical]
        print(f"  OK    {canonical.name} → {chapter_id}.md ({len(content.split())} words)")
        if skipped:
            print(f"        Skipped older versions: {', '.join(skipped)}")

    print("\nDone. Original files in notes/drafts/ are untouched.")
    print("Run 'klemma-cli push' to sync draft/ to the server.")


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    migrate(root)
