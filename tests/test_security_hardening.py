"""Security hardening tests for vault path boundaries, acquire limits, and file store."""

from pathlib import Path

import pytest

from klemma.literature.url_safety import is_safe_url
from klemma.literature.web import fetch_url_text
from klemma.skills import acquirer
from klemma.stores.file_store import LocalFileStore
from klemma.vault import VaultAdapter


class _FakeResponse:
    def __init__(self, chunks: list[bytes], headers: dict[str, str], status_code: int = 200):
        self._chunks = chunks
        self.headers = headers
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int = 8192):
        del chunk_size
        yield from self._chunks


class _TmpFile:
    def __init__(self, path: Path):
        self.name = str(path)
        self._fh = open(path, "wb")

    def write(self, chunk: bytes):
        self._fh.write(chunk)

    def close(self):
        self._fh.close()


def test_vault_create_note_rejects_folder_traversal(tmp_path: Path):
    vault = VaultAdapter(str(tmp_path / "vault"), use_cli=False)
    with pytest.raises(ValueError, match="escapes vault root"):
        vault.create_note("note", "content", folder="../../outside")


def test_vault_create_note_rejects_name_traversal(tmp_path: Path):
    vault = VaultAdapter(str(tmp_path / "vault"), use_cli=False)
    with pytest.raises(ValueError, match="escapes vault root"):
        vault.create_note("../outside", "content")


def test_vault_create_note_allows_valid_path(tmp_path: Path):
    vault = VaultAdapter(str(tmp_path / "vault"), use_cli=False)
    path = vault.create_note("safe", "ok", folder="Notes")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "ok"
    assert path.parent == (tmp_path / "vault" / "Notes").resolve()


def test_is_safe_url_blocks_private_ips():
    assert not is_safe_url("http://localhost/data")
    assert not is_safe_url("http://127.0.0.1/data")
    assert not is_safe_url("http://192.168.1.1/data")
    assert not is_safe_url("http://10.0.0.1/data")
    assert not is_safe_url("ftp://example.com/data")
    assert not is_safe_url("file:///etc/passwd")
    assert is_safe_url("https://example.com/data")
    assert is_safe_url("https://arxiv.org/pdf/2101.12345.pdf")


def test_fetch_url_text_rejects_unsafe_url(monkeypatch):
    """fetch_url_text must block SSRF-prone URLs without making a request."""
    import requests as requests_mod

    def _never_called(*args, **kwargs):
        raise AssertionError("requests.get must not be called for blocked URL")

    monkeypatch.setattr(requests_mod, "get", _never_called)
    assert fetch_url_text("http://localhost/secret") == ""
    assert fetch_url_text("http://127.0.0.1/secret") == ""
    assert fetch_url_text("http://192.168.1.1/internal") == ""
    assert fetch_url_text("ftp://example.com/file") == ""


def test_download_pdf_rejects_unsafe_url(monkeypatch):
    def _never_called(*args, **kwargs):
        raise AssertionError("requests.get must not be called for blocked URL")

    monkeypatch.setattr(acquirer.requests, "get", _never_called)
    assert acquirer.download_pdf("ftp://example.com/file.pdf") is None
    assert acquirer.download_pdf("http://localhost/file.pdf") is None
    assert acquirer.download_pdf("http://127.0.0.1/file.pdf") is None


def test_download_pdf_rejects_large_content_length(monkeypatch):
    def _fake_get(*args, **kwargs):
        del args, kwargs
        return _FakeResponse(
            chunks=[b"x" * 8192],
            headers={"content-length": str(60 * 1024 * 1024), "content-type": "application/pdf"},
        )

    monkeypatch.setattr(acquirer.requests, "get", _fake_get)
    assert acquirer.download_pdf("https://example.com/file.pdf", max_bytes=1024) is None


def test_download_pdf_removes_temp_file_when_stream_exceeds_limit(monkeypatch, tmp_path: Path):
    tmp_file = tmp_path / "download.pdf"

    def _fake_get(*args, **kwargs):
        del args, kwargs
        return _FakeResponse(
            chunks=[b"x" * 9000, b"y" * 9000],
            headers={"content-type": "application/pdf"},
        )

    def _fake_named_tempfile(**kwargs):
        del kwargs
        return _TmpFile(tmp_file)

    monkeypatch.setattr(acquirer.requests, "get", _fake_get)
    monkeypatch.setattr(acquirer.tempfile, "NamedTemporaryFile", _fake_named_tempfile)

    result = acquirer.download_pdf("https://example.com/file.pdf", max_bytes=10_000)
    assert result is None
    assert not tmp_file.exists()


# --- LocalFileStore path traversal ---


def test_file_store_rejects_dotdot_in_paper_id(tmp_path: Path):
    store = LocalFileStore(tmp_path / "files")
    with pytest.raises(ValueError, match="Invalid paper_id"):
        store.save("../evil", b"data", "file.pdf")


def test_file_store_rejects_slash_in_paper_id(tmp_path: Path):
    store = LocalFileStore(tmp_path / "files")
    with pytest.raises(ValueError, match="Invalid paper_id"):
        store.save("ab/cd", b"data", "file.pdf")


def test_file_store_rejects_dotdot_in_filename(tmp_path: Path):
    store = LocalFileStore(tmp_path / "files")
    with pytest.raises(ValueError, match="Invalid filename"):
        store.save("abcdef12", b"data", "../outside.pdf")


def test_file_store_rejects_slash_in_filename(tmp_path: Path):
    store = LocalFileStore(tmp_path / "files")
    with pytest.raises(ValueError, match="Invalid filename"):
        store.save("abcdef12", b"data", "sub/file.pdf")


def test_file_store_rejects_empty_paper_id(tmp_path: Path):
    store = LocalFileStore(tmp_path / "files")
    with pytest.raises(ValueError, match="Invalid paper_id"):
        store.save("", b"data", "file.pdf")


def test_file_store_allows_valid_ids(tmp_path: Path):
    store = LocalFileStore(tmp_path / "files")
    result = store.save("abcdef1234567890", b"hello", "paper.pdf")
    assert Path(result).is_file()
    assert store.read("abcdef1234567890", "paper.pdf") == b"hello"

