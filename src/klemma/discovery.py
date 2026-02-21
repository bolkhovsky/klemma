"""Auto-discovery of external tool paths for `klemma init` interactive setup."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


def discover_obsidian_vault() -> Optional[Path]:
    """Find Obsidian vault by searching common locations for .obsidian/ dir."""
    home = Path.home()
    candidates = [
        home / "Documents" / "Obsidian Vault",
        home / "Obsidian Vault",
        home / "Obsidian",
    ]
    # Check known candidates first
    for path in candidates:
        if (path / ".obsidian").is_dir():
            return path

    # Scan ~/Documents/ one level deep
    docs = home / "Documents"
    if docs.is_dir():
        try:
            for child in docs.iterdir():
                if child.is_dir() and (child / ".obsidian").is_dir():
                    return child
        except PermissionError:
            pass

    # macOS: check Obsidian app config for vault list
    obsidian_config = home / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    if obsidian_config.is_file():
        try:
            data = json.loads(obsidian_config.read_text(encoding="utf-8"))
            vaults = data.get("vaults", {})
            for vault_info in vaults.values():
                vault_path = Path(vault_info.get("path", ""))
                if vault_path.is_dir() and (vault_path / ".obsidian").is_dir():
                    return vault_path
        except (json.JSONDecodeError, KeyError, PermissionError):
            pass

    return None


def discover_zotero_storage() -> Optional[Path]:
    """Check if ~/Zotero/storage exists (default Zotero data dir)."""
    storage = Path.home() / "Zotero" / "storage"
    return storage if storage.is_dir() else None


def discover_bbt_json() -> Optional[Path]:
    """Search common locations for BetterBibTeX JSON exports."""
    home = Path.home()
    search_dirs = [
        home / "Zotero",
        home / "research",
        home / "Documents",
    ]
    for search_dir in search_dirs:
        result = _find_bbt_in_dir(search_dir, max_depth=2)
        if result:
            return result
    return None


def _find_bbt_in_dir(directory: Path, max_depth: int = 2) -> Optional[Path]:
    """Recursively search a directory for BBT JSON files up to max_depth."""
    if not directory.is_dir():
        return None
    try:
        pattern = "*.json" if max_depth <= 1 else "**/*.json"
        candidates = []
        for f in directory.glob(pattern):
            if f.name.startswith("."):
                continue
            # Limit depth
            rel = f.relative_to(directory)
            if len(rel.parts) > max_depth:
                continue
            candidates.append(f)
        # Sort by modification time (newest first)
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for f in candidates:
            if _is_bbt_json(f):
                return f
    except PermissionError:
        pass
    return None


def _is_bbt_json(path: Path) -> bool:
    """Quick check if a JSON file looks like a BBT export."""
    try:
        with open(path, encoding="utf-8") as fh:
            head = fh.read(500)
        return '"citationKey"' in head or '"citekey"' in head or '"itemType"' in head
    except (OSError, UnicodeDecodeError):
        return False


def discover_relevant_sources(
    vault_path: Path,
    notes_folder: str,
    library_entries: dict,
    keywords: list[str],
    description: str = "",
) -> list[dict]:
    """Find sources matching keywords by scanning vault notes and BBT entries.

    Matching strategy (score each source):
    - Keyword in vault note tags → +3
    - Keyword in BBT entry title → +2
    - Keyword in BBT entry abstract → +1
    - Keyword in BBT entry keywords field → +2

    Args:
        vault_path: Path to Obsidian vault
        notes_folder: Subfolder with @citekey.md notes
        library_entries: {citekey: ZoteroEntry} from BBT JSON
        keywords: User-provided keywords for matching
        description: Optional research description (words used as extra keywords)

    Returns:
        Sorted list of {citekey, title, score} with score > 0.
    """
    if not keywords and not description:
        return []

    # Build search terms: explicit keywords + words from description (3+ chars)
    terms = [k.lower() for k in keywords]
    if description:
        for word in description.split():
            w = word.strip(".,;:!?()\"'").lower()
            if len(w) >= 4 and w not in terms:
                terms.append(w)

    if not terms:
        return []

    # Scan vault note tags
    vault_tags: dict[str, list[str]] = {}  # citekey → [tags]
    notes_dir = vault_path / notes_folder
    if notes_dir.is_dir():
        try:
            for note in notes_dir.iterdir():
                if not note.name.startswith("@") or not note.name.endswith(".md"):
                    continue
                citekey = note.stem.lstrip("@")
                tags = _extract_vault_tags(note)
                if tags:
                    vault_tags[citekey] = tags
        except PermissionError:
            pass

    # Score each BBT entry
    results = []
    for citekey, entry in library_entries.items():
        score = 0
        title_lower = (getattr(entry, "title", "") or "").lower()
        abstract_lower = (getattr(entry, "abstract", "") or "").lower()
        kw_field = (getattr(entry, "keywords", "") or "").lower()
        note_tags = [t.lower() for t in vault_tags.get(citekey, [])]

        for term in terms:
            if any(term in tag for tag in note_tags):
                score += 3
            if term in title_lower:
                score += 2
            if term in kw_field:
                score += 2
            if term in abstract_lower:
                score += 1

        if score > 0:
            results.append({
                "citekey": citekey,
                "title": getattr(entry, "title", "") or citekey,
                "score": score,
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def _extract_vault_tags(note_path: Path) -> list[str]:
    """Extract tags from vault note YAML frontmatter."""
    try:
        text = note_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    if not text.startswith("---"):
        return []
    end = text.find("---", 3)
    if end == -1:
        return []
    try:
        import yaml
        fm = yaml.safe_load(text[3:end]) or {}
        tags = fm.get("tags", [])
        return tags if isinstance(tags, list) else []
    except Exception:
        return []


def detect_language() -> str:
    """Detect 2-letter language code from system locale."""
    for var in ("LANG", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(var, "")
        if val and len(val) >= 2:
            code = val[:2].lower()
            if code.isalpha():
                return code
    return "ru"
