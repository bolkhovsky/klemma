"""Obsidian vault adapter — CLI with file I/O fallback."""

import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Optional


class VaultAdapter:
    """Abstracts Obsidian vault operations. Uses CLI when available, file I/O otherwise."""

    def __init__(self, vault_path: str, use_cli: Optional[bool] = None):
        self.vault_path = Path(vault_path)
        self.use_cli = use_cli if use_cli is not None else self._detect_cli()

    @staticmethod
    def _detect_cli() -> bool:
        return shutil.which("obsidian") is not None

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
        target_dir = self.vault_path / folder if folder else self.vault_path
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{name}.md"
        path.write_text(content, encoding="utf-8")
        return path

    def append_to_note(self, name: str, content: str, folder: Optional[str] = None) -> Optional[Path]:
        """Append content to an existing note."""
        target_dir = self.vault_path / folder if folder else self.vault_path
        path = target_dir / f"{name}.md"
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
        target_dir = self.vault_path / folder
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
            d = self.vault_path / candidate
            if d.exists():
                return d
        # Fallback: create in root
        d = self.vault_path / "Daily"
        d.mkdir(exist_ok=True)
        return d

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
