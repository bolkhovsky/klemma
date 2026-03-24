"""Git subprocess wrappers for klemma-cli sync operations."""

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


def add_remote(path: Path, name: str, url: str) -> None:
    """Add a git remote. Removes existing remote with same name first."""
    # Check if remote exists
    result = _run(["remote", "get-url", name], cwd=path, check=False)
    if result.returncode == 0:
        _run(["remote", "set-url", name, url], cwd=path)
    else:
        _run(["remote", "add", name, url], cwd=path)


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
        if "nothing to commit" in result.stdout + result.stderr:
            return None
        raise GitError("commit", result.stderr.strip())
    # Extract commit hash
    hash_result = _run(["rev-parse", "HEAD"], cwd=path)
    return hash_result.stdout.strip()


def push(path: Path, remote: str = "klemma", branch: str = "main") -> bool:
    """Push to remote. Returns True on success, False if rejected."""
    result = _run(["push", remote, branch], cwd=path, check=False)
    if result.returncode != 0:
        if "rejected" in result.stderr or "non-fast-forward" in result.stderr:
            return False
        raise GitError("push", result.stderr.strip())
    return True


def pull(path: Path, remote: str = "klemma", branch: str = "main") -> str:
    """Pull from remote. Returns output text."""
    result = _run(["pull", remote, branch, "--no-rebase"], cwd=path, check=False)
    if result.returncode != 0:
        if "CONFLICT" in result.stdout + result.stderr:
            return f"CONFLICT: {result.stdout}\n{result.stderr}"
        raise GitError("pull", result.stderr.strip())
    return result.stdout.strip()


def fetch(path: Path, remote: str = "klemma") -> None:
    """Fetch from remote."""
    _run(["fetch", remote], cwd=path, check=False)


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


def status(path: Path) -> str:
    """Return git status output."""
    result = _run(["status", "--short"], cwd=path, check=False)
    return result.stdout.strip()


def remote_log(path: Path, remote: str = "klemma", branch: str = "main") -> list[str]:
    """Return commits on remote that are not in local HEAD."""
    fetch(path, remote)
    result = _run(
        ["log", f"HEAD..{remote}/{branch}", "--oneline"],
        cwd=path,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.strip().split("\n") if line]


def revert_last_n(path: Path, n: int) -> None:
    """Revert the last N commits (creates revert commits)."""
    if n <= 0:
        return
    _run(["revert", "--no-edit", f"HEAD~{n}..HEAD"], cwd=path)


def force_push(path: Path, remote: str = "klemma", branch: str = "main") -> None:
    """Force push with lease (safe force push)."""
    _run(["push", "--force-with-lease", remote, branch], cwd=path)


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
