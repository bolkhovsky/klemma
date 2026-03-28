"""Git subprocess wrappers for local klemma-cli operations.

Only local git operations — no server transport (push/pull to remote).
Server sync is handled exclusively via the REST API (sync.py).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    """Raised when a git command fails."""

    def __init__(self, cmd: str, stderr: str) -> None:
        self.cmd = cmd
        self.stderr = stderr
        super().__init__(f"git {cmd} failed: {stderr}")


def _run(
    args: list[str],
    cwd: Path,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    """Run a git command."""
    result = subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        input=input_text,
        timeout=60,
    )
    if check and result.returncode != 0:
        raise GitError(args[0], result.stderr.strip())
    return result


def is_git_repo(path: Path) -> bool:
    """Check if path is inside a git repository."""
    result = _run(["rev-parse", "--git-dir"], cwd=path, check=False)
    return result.returncode == 0


def init(path: Path) -> None:
    """Initialize a git repository at path."""
    _run(["init"], cwd=path)


def add_files(path: Path, patterns: list[str]) -> None:
    """Stage files matching patterns."""
    for pattern in patterns:
        _run(["add", pattern], cwd=path, check=False)


def has_changes(path: Path) -> bool:
    """Check if there are staged or unstaged changes."""
    result = _run(["status", "--porcelain"], cwd=path)
    return bool(result.stdout.strip())


def commit(path: Path, message: str) -> str | None:
    """Create a commit with the given message. Returns commit hash or None if nothing to commit."""
    result = _run(["commit", "-m", message], cwd=path, check=False)
    if result.returncode != 0:
        combined = result.stdout + result.stderr
        if "nothing to commit" in combined or "nothing added to commit" in combined:
            return None
        # Empty stderr with non-zero exit = also nothing to commit (no staged files)
        if not result.stderr.strip():
            return None
        raise GitError("commit", result.stderr.strip())
    # Extract commit hash
    hash_result = _run(["rev-parse", "HEAD"], cwd=path)
    return hash_result.stdout.strip()


def log(path: Path, count: int = 10, format_str: str = "%h %s") -> list[str]:
    """Return recent commit log entries."""
    result = _run(
        ["log", f"-{count}", f"--format={format_str}"],
        cwd=path,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.strip().split("\n") if line]


_SYNCED_PREFIXES = ("KLEMMA.md", "draft/", "notes/research/", ".gitignore")


def status(path: Path) -> str:
    """Return git status filtered to klemma-synced paths only."""
    result = _run(["status", "--short"], cwd=path, check=False)
    lines = [
        line for line in result.stdout.splitlines()
        if len(line) > 3 and any(line[3:].startswith(p) for p in _SYNCED_PREFIXES)
    ]
    return "\n".join(lines)


def get_head_hash(path: Path) -> str | None:
    """Return HEAD commit hash or None if no commits."""
    result = _run(["rev-parse", "HEAD"], cwd=path, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def write_gitignore(path: Path) -> None:
    """Create/update .gitignore with klemma-specific exclusions."""
    gitignore = path / ".gitignore"
    entries = {
        ".klemma/data/",
        ".klemma/sync_config.json",
        "*.pdf",
        "*.db",
        "*.db-wal",
        "*.db-shm",
        "__pycache__/",
        ".DS_Store",
    }

    existing: set[str] = set()
    if gitignore.exists():
        existing = {
            line.strip()
            for line in gitignore.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        }

    new_entries = entries - existing
    if new_entries:
        with open(gitignore, "a") as f:
            if existing:
                f.write("\n")
            f.write("# klemma-cli sync\n")
            for entry in sorted(new_entries):
                f.write(f"{entry}\n")
