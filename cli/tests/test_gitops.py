"""Tests for gitops — git subprocess wrappers."""

from __future__ import annotations

import subprocess

import pytest

from klemma_cli.gitops import (
    add_files,
    commit,
    get_head_hash,
    has_changes,
    init,
    is_git_repo,
    log,
    status,
    write_gitignore,
)


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repo."""
    init(tmp_path)
    # Configure git user for commits
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), capture_output=True,
    )
    return tmp_path


class TestIsGitRepo:
    def test_is_git_repo(self, git_repo):
        assert is_git_repo(git_repo) is True

    def test_not_git_repo(self, tmp_path):
        assert is_git_repo(tmp_path) is False


class TestInit:
    def test_init_creates_repo(self, tmp_path):
        init(tmp_path)
        assert (tmp_path / ".git").exists()


class TestCommit:
    def test_commit_with_changes(self, git_repo):
        (git_repo / "test.md").write_text("hello")
        add_files(git_repo, ["test.md"])
        h = commit(git_repo, "test commit")
        assert h is not None
        assert len(h) == 40  # full SHA

    def test_commit_nothing_to_commit(self, git_repo):
        # Need at least one commit for an empty repo to work
        (git_repo / "init.md").write_text("init")
        add_files(git_repo, ["init.md"])
        commit(git_repo, "initial")

        h = commit(git_repo, "empty commit")
        assert h is None


class TestStatus:
    def test_clean_status(self, git_repo):
        s = status(git_repo)
        assert s == ""

    def test_non_synced_file_hidden(self, git_repo):
        """Files outside synced paths are not shown."""
        (git_repo / "random_file.md").write_text("not synced")
        s = status(git_repo)
        assert s == ""

    def test_synced_file_shown(self, git_repo):
        """Files under synced paths (KLEMMA.md, draft/, notes/research/) are shown."""
        (git_repo / "KLEMMA.md").write_text("project metadata")
        (git_repo / "draft").mkdir()
        (git_repo / "draft" / "chapter_1.md").write_text("chapter text")
        s = status(git_repo)
        assert "KLEMMA.md" in s
        assert "draft/" in s
        assert "random_file.md" not in s


class TestHasChanges:
    def test_no_changes(self, git_repo):
        assert has_changes(git_repo) is False

    def test_with_changes(self, git_repo):
        (git_repo / "file.txt").write_text("content")
        assert has_changes(git_repo) is True


class TestLog:
    def test_log_empty(self, tmp_path):
        init(tmp_path)
        entries = log(tmp_path)
        assert entries == []

    def test_log_with_commits(self, git_repo):
        (git_repo / "a.md").write_text("a")
        add_files(git_repo, ["a.md"])
        commit(git_repo, "first commit")
        entries = log(git_repo, count=5)
        assert len(entries) == 1
        assert "first commit" in entries[0]


class TestGetHeadHash:
    def test_no_commits(self, tmp_path):
        init(tmp_path)
        assert get_head_hash(tmp_path) is None

    def test_with_commit(self, git_repo):
        (git_repo / "x.md").write_text("x")
        add_files(git_repo, ["x.md"])
        commit(git_repo, "test")
        h = get_head_hash(git_repo)
        assert h is not None
        assert len(h) == 40


class TestWriteGitignore:
    def test_creates_gitignore(self, tmp_path):
        write_gitignore(tmp_path)
        gi = (tmp_path / ".gitignore").read_text()
        assert ".klemma/data/" in gi
        assert "*.pdf" in gi
        assert "*.db" in gi

    def test_appends_to_existing(self, tmp_path):
        (tmp_path / ".gitignore").write_text("node_modules/\n")
        write_gitignore(tmp_path)
        gi = (tmp_path / ".gitignore").read_text()
        assert "node_modules/" in gi
        assert ".klemma/data/" in gi

    def test_idempotent(self, tmp_path):
        write_gitignore(tmp_path)
        content1 = (tmp_path / ".gitignore").read_text()
        write_gitignore(tmp_path)
        content2 = (tmp_path / ".gitignore").read_text()
        assert content1 == content2
