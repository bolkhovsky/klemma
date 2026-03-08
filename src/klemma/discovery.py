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
            # Skip BBT internal cache files (e.g. cache.itemToExportFormat.json)
            if f.name.startswith("cache."):
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
            head = fh.read(4000)
        # BBT JSON export header contains this label
        if '"BetterBibTeX JSON"' in head:
            return True
        return '"citationKey"' in head or '"citekey"' in head or '"itemType"' in head
    except (OSError, UnicodeDecodeError):
        return False


def discover_relevant_sources(
    vault_path: Path,
    notes_folder: str,
    library_entries: dict,
    keywords: list[str],
    description: str = "",
    embeddings=None,
    state=None,
    query_title: str = "",
) -> list[dict]:
    """Find sources matching keywords by scanning vault notes and BBT entries.

    Matching strategy (score each source):
    - Keyword in vault note tags → +3
    - Keyword in BBT entry title → +2
    - Keyword in BBT entry abstract → +1
    - Keyword in BBT entry keywords field → +2

    When embeddings and state are provided, uses hybrid scoring:
    combined = 0.4 × (kw_score / max_kw) + 0.6 × cosine_similarity

    Args:
        vault_path: Path to Obsidian vault
        notes_folder: Subfolder with @citekey.md notes
        library_entries: {citekey: ZoteroEntry} from BBT JSON
        keywords: User-provided keywords for matching
        description: Optional research description (words used as extra keywords)
        embeddings: Optional EmbeddingProvider for semantic scoring
        state: Optional StateManager for retrieving stored embeddings
        query_title: Optional title/query for embedding (used for semantic search)

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

    # Hybrid scoring: combine keyword + semantic when embeddings available
    if embeddings and state and (query_title or description):
        from .embeddings import cosine_similarity

        query_text = query_title or description
        query_vec = embeddings.embed(query_text, description or "")
        if query_vec:
            all_emb = state.get_all_embeddings(model=embeddings.model_name)
            max_kw = max((r["score"] for r in results), default=1) or 1

            # Add semantically close sources that keyword search missed
            for citekey, stored_vec in all_emb.items():
                sim = cosine_similarity(query_vec, stored_vec)
                if sim > 0.3 and not any(r["citekey"] == citekey for r in results):
                    entry = library_entries.get(citekey)
                    results.append({
                        "citekey": citekey,
                        "title": getattr(entry, "title", "") or citekey if entry else citekey,
                        "score": 0,
                    })

            # Recompute hybrid scores
            for r in results:
                kw_norm = r["score"] / max_kw
                stored_vec = all_emb.get(r["citekey"])
                if stored_vec:
                    sim = cosine_similarity(query_vec, stored_vec)
                    r["score"] = round(0.4 * kw_norm + 0.6 * sim, 4)
                    r["semantic_sim"] = round(sim, 4)
                else:
                    # No embedding — use keyword score only (normalized)
                    r["score"] = round(kw_norm, 4)

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


def discover_vault_folders(vault_path: Path) -> tuple[str, str]:
    """Detect notes_folder and tags_folder by scanning vault subdirectories.

    notes_folder: subdir containing the most "@"-prefixed .md files (citekey notes).
    tags_folder:  subdir whose name contains "tag" (case-insensitive).

    Returns ("", "") if nothing detected.
    """
    notes_folder = ""
    tags_folder = ""

    if not vault_path.is_dir():
        return notes_folder, tags_folder

    try:
        subdirs = [d for d in vault_path.iterdir() if d.is_dir() and not d.name.startswith(".")]
    except OSError:
        return notes_folder, tags_folder

    # notes_folder: subdir with most @citekey.md files
    best_count = 0
    for d in subdirs:
        try:
            count = sum(1 for f in d.iterdir() if f.suffix == ".md" and f.stem.startswith("@"))
        except OSError:
            count = 0
        if count > best_count:
            best_count = count
            notes_folder = d.name

    # tags_folder: subdir whose name contains "tag" (case-insensitive)
    for d in subdirs:
        if "tag" in d.name.lower():
            tags_folder = d.name
            break

    return notes_folder, tags_folder


def detect_language() -> str:
    """Detect 2-letter language code from system locale."""
    for var in ("LANG", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(var, "")
        if val and len(val) >= 2:
            code = val[:2].lower()
            if code.isalpha():
                return code
    return "ru"
