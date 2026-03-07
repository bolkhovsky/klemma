"""Obsidian vault adapter — CLI with file I/O fallback."""

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Optional


class VaultAdapter:
    """Abstracts Obsidian vault operations. Uses CLI when available, file I/O otherwise."""

    def __init__(self, vault_path: str, use_cli: Optional[bool] = None):
        self.vault_path = Path(vault_path).expanduser().resolve()
        self.use_cli = use_cli if use_cli is not None else self._detect_cli()

    @staticmethod
    def _detect_cli() -> bool:
        return shutil.which("obsidian") is not None

    def _ensure_within_vault(self, path: Path) -> Path:
        """Validate that a resolved path stays inside the vault root."""
        root = self.vault_path
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"Path escapes vault root: {path}")
        return resolved

    def _resolve_folder(self, folder: Optional[str] = None) -> Path:
        """Resolve a folder path inside the vault root."""
        if not folder:
            return self.vault_path
        return self._ensure_within_vault(self.vault_path / folder)

    def check_folder(self, folder: str) -> bool:
        """Check if a subfolder exists in the vault."""
        return self._resolve_folder(folder).is_dir()

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Search vault for notes matching query."""
        if self.use_cli:
            try:
                result = subprocess.run(
                    ["obsidian", "search", f"query={query}", "format=json"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    return json.loads(result.stdout)[:limit]
            except Exception:
                pass

        # File I/O fallback: grep through markdown files
        results = []
        query_lower = query.lower()
        for md_path in self.vault_path.rglob("*.md"):
            try:
                text = md_path.read_text(encoding="utf-8")
                if query_lower in text.lower():
                    results.append({
                        "path": str(md_path.relative_to(self.vault_path)),
                        "name": md_path.stem,
                    })
                    if len(results) >= limit:
                        break
            except Exception:
                continue
        return results

    def read_note(self, name: str) -> Optional[str]:
        """Read a note's content by name (without .md extension)."""
        if self.use_cli:
            try:
                result = subprocess.run(
                    ["obsidian", "read", f"file={name}"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    return result.stdout
            except Exception:
                pass

        # File I/O fallback
        for md_path in self.vault_path.rglob(f"{name}.md"):
            try:
                return md_path.read_text(encoding="utf-8")
            except Exception:
                continue
        return None

    def create_note(
        self, name: str, content: str, folder: Optional[str] = None
    ) -> Path:
        """Create or overwrite a note."""
        if self.use_cli and not folder:
            try:
                subprocess.run(
                    ["obsidian", "create", f"name={name}", f"content={content}", "silent"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception:
                pass

        # File I/O (always write to ensure file exists)
        target_dir = self._resolve_folder(folder)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = self._ensure_within_vault(target_dir / f"{name}.md")
        path.write_text(content, encoding="utf-8")
        return path

    def update_section(self, name: str, section_heading: str, new_content: str) -> Optional[Path]:
        """Replace a section's content in an existing note.

        Finds the heading (e.g. '## 💬 Цитаты для диссертации') and replaces
        everything until the next '---' separator or same/higher-level heading.
        """
        text = self.read_note(name)
        if not text:
            return None

        idx = text.find(section_heading)
        if idx == -1:
            return None

        # Find end of section: next '---' or next heading of same/higher level
        heading_level = 0
        for ch in section_heading:
            if ch == "#":
                heading_level += 1
            else:
                break

        after = idx + len(section_heading)
        end_idx = len(text)
        lines = text[after:].split("\n")
        offset = after
        for line in lines:
            stripped = line.lstrip()
            # Stop at --- separator
            if stripped.startswith("---"):
                end_idx = offset
                break
            # Stop at same/higher-level heading
            if stripped.startswith("#"):
                level = len(stripped) - len(stripped.lstrip("#"))
                if level <= heading_level:
                    end_idx = offset
                    break
            offset += len(line) + 1

        updated = text[:idx] + section_heading + "\n\n" + new_content + "\n\n" + text[end_idx:]

        # Find the file and write
        for md_path in self.vault_path.rglob(f"{name}.md"):
            md_path.write_text(updated, encoding="utf-8")
            return md_path
        return None

    def append_to_note(self, name: str, content: str, folder: Optional[str] = None) -> Optional[Path]:
        """Append content to an existing note."""
        target_dir = self._resolve_folder(folder)
        path = self._ensure_within_vault(target_dir / f"{name}.md")
        if not path.exists():
            return None
        existing = path.read_text(encoding="utf-8")
        path.write_text(existing + "\n" + content, encoding="utf-8")
        return path

    def append_to_daily(self, content: str) -> Path:
        """Append content to today's daily note."""
        today = date.today()
        daily_name = today.strftime("%Y-%m-%d")

        if self.use_cli:
            try:
                subprocess.run(
                    ["obsidian", "daily:append", f"content={content}", "silent"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception:
                pass

        # File I/O fallback — find daily notes folder
        daily_dir = self._find_daily_dir()
        path = daily_dir / f"{daily_name}.md"

        if path.exists():
            existing = path.read_text(encoding="utf-8")
            path.write_text(existing + "\n\n" + content, encoding="utf-8")
        else:
            path.write_text(f"# {daily_name}\n\n{content}", encoding="utf-8")
        return path

    def get_properties(self, name: str) -> dict:
        """Read YAML frontmatter properties from a note."""
        text = self.read_note(name)
        if not text:
            return {}
        return self._parse_frontmatter(text)

    def list_notes(self, folder: str) -> list[str]:
        """List note names in a folder."""
        target_dir = self._resolve_folder(folder)
        if not target_dir.exists():
            return []
        return [p.stem for p in sorted(target_dir.glob("*.md"))]

    def get_tags(self) -> dict[str, int]:
        """Get all tags with counts from vault."""
        if self.use_cli:
            try:
                result = subprocess.run(
                    ["obsidian", "tags", "all", "counts"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0:
                    return json.loads(result.stdout)
            except Exception:
                pass

        # File I/O fallback — scan frontmatter
        tags: dict[str, int] = {}
        for md_path in self.vault_path.rglob("*.md"):
            try:
                text = md_path.read_text(encoding="utf-8")
                props = self._parse_frontmatter(text)
                for tag in props.get("tags", []):
                    tags[tag] = tags.get(tag, 0) + 1
            except Exception:
                continue
        return tags

    def _find_daily_dir(self) -> Path:
        """Find daily notes directory."""
        # Common patterns
        for candidate in ["Daily", "0 - Atlas/Daily", "Journal", "daily"]:
            d = self._resolve_folder(candidate)
            if d.exists():
                return d
        # Fallback: create in root
        d = self._resolve_folder("Daily")
        d.mkdir(exist_ok=True)
        return d

    def update_frontmatter_sections(
        self, name: str, sections: list[str], folder: Optional[str] = None,
    ) -> bool:
        """Update the 'sections' list in a note's YAML frontmatter.

        Rewrites the frontmatter block with the new sections list.
        Returns True if the file was modified, False if not found.
        """
        import yaml

        # Find the file
        if folder:
            target = self._resolve_folder(folder) / f"{name}.md"
            if not target.exists():
                return False
        else:
            found = list(self.vault_path.rglob(f"{name}.md"))
            if not found:
                return False
            target = found[0]

        text = target.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return False
        end = text.find("---", 3)
        if end == -1:
            return False

        fm = yaml.safe_load(text[3:end]) or {}
        fm["sections"] = sorted(sections, key=lambda s: [int(x) for x in s.split(".")])

        new_fm = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
        new_text = f"---\n{new_fm}---{text[end + 3:]}"
        target.write_text(new_text, encoding="utf-8")
        return True

    @staticmethod
    def _parse_frontmatter(text: str) -> dict:
        """Extract YAML frontmatter from markdown text."""
        if not text.startswith("---"):
            return {}
        end = text.find("---", 3)
        if end == -1:
            return {}
        import yaml
        try:
            return yaml.safe_load(text[3:end]) or {}
        except Exception:
            return {}
