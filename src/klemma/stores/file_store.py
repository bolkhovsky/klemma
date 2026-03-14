"""Local filesystem implementation of FileStore (ADR-009).

Stores files in a content-addressed directory structure:
  {base_dir}/{paper_id_prefix}/{paper_id}/{filename}

paper_id_prefix is the first 2 characters of paper_id for
directory fan-out (avoids too many entries in a single dir).
"""

from __future__ import annotations

import shutil
from pathlib import Path


class LocalFileStore:
    """Filesystem-backed FileStore at a configurable base directory.

    Default location: ~/.klemma/files/ (or /data/klemma/pdfs/ on server).
    """

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, paper_id: str, filename: str) -> Path:
        prefix = paper_id[:2]
        return self.base_dir / prefix / paper_id / filename

    def save(self, paper_id: str, data: bytes, filename: str) -> str:
        """Save file data, return storage path."""
        path = self._file_path(paper_id, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def read(self, paper_id: str, filename: str) -> bytes | None:
        """Read file data. Returns None if not found."""
        path = self._file_path(paper_id, filename)
        if not path.is_file():
            return None
        return path.read_bytes()

    def exists(self, paper_id: str, filename: str) -> bool:
        """Check if a file exists."""
        return self._file_path(paper_id, filename).is_file()

    def delete(self, paper_id: str, filename: str) -> bool:
        """Delete a file. Returns True if deleted."""
        path = self._file_path(paper_id, filename)
        if not path.is_file():
            return False
        path.unlink()
        # Clean up empty parent dirs
        parent = path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
        return True

    def get_path(self, paper_id: str, filename: str) -> str | None:
        """Get filesystem path for the file."""
        path = self._file_path(paper_id, filename)
        if not path.is_file():
            return None
        return str(path)

    def get_paper_dir(self, paper_id: str) -> Path:
        """Get the directory for a paper's files."""
        prefix = paper_id[:2]
        return self.base_dir / prefix / paper_id

    def delete_paper_files(self, paper_id: str) -> int:
        """Delete all files for a paper. Returns count deleted."""
        paper_dir = self.get_paper_dir(paper_id)
        if not paper_dir.is_dir():
            return 0
        count = sum(1 for f in paper_dir.iterdir() if f.is_file())
        shutil.rmtree(paper_dir)
        return count
