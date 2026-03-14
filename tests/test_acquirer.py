"""Tests for acquire library dedup (ADR-014 Phase 1E)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from klemma.models import PaperRecord
from klemma.skills.acquirer import PaperMetadata, acquire_paper_local
from klemma.state import StateManager


def _make_paper_record(**kwargs) -> PaperRecord:
    defaults = dict(
        paper_id="uuid-paper-1",
        pdf_hash="abc123hash",
        doi="10.1234/test",
        title="Test Paper Title",
        authors="Smith, John",
        year=2023,
        abstract="Test abstract",
    )
    defaults.update(kwargs)
    return PaperRecord(**defaults)


# ── DOI pre-check ──────────────────────────────────────────────────────────


def test_acquire_doi_dedup_returns_ok_library_doi():
    """DOI already in library → returns ok_library_doi without downloading."""
    record = _make_paper_record()
    mock_paper_store = MagicMock()
    mock_paper_store.find_paper.return_value = record
    mock_user_library = MagicMock()

    meta = PaperMetadata(url="https://doi.org/10.1234/test")
    result = acquire_paper_local(
        meta, storage_path="", state=None,
        paper_store=mock_paper_store, user_library=mock_user_library,
    )

    assert result.status == "ok_library_doi"
    assert result.pdf_hash == "abc123hash"
    mock_paper_store.find_paper.assert_called_with(doi="10.1234/test")


def test_acquire_doi_dedup_skips_download():
    """DOI dedup should never call download_pdf."""
    record = _make_paper_record()
    mock_paper_store = MagicMock()
    mock_paper_store.find_paper.return_value = record

    meta = PaperMetadata(url="https://doi.org/10.1234/test")
    with patch("klemma.skills.acquirer.download_pdf") as mock_dl:
        acquire_paper_local(meta, storage_path="", paper_store=mock_paper_store)
        mock_dl.assert_not_called()


def test_acquire_doi_dedup_registers_in_state(tmp_path):
    """DOI dedup registers citekey in local state DB."""
    db = tmp_path / "state.db"
    sm = StateManager(db)

    record = _make_paper_record()
    mock_paper_store = MagicMock()
    mock_paper_store.find_paper.return_value = record

    meta = PaperMetadata(url="https://doi.org/10.1234/test")
    result = acquire_paper_local(
        meta, storage_path="", state=sm, paper_store=mock_paper_store,
    )

    assert result.status == "ok_library_doi"
    # get_all_sources() filters by status=completed; use get_source() instead
    src = sm.get_source(result.citekey)
    assert src is not None, f"citekey {result.citekey!r} not found in DB"


def test_acquire_doi_dedup_registers_in_user_library():
    """DOI dedup calls user_library.add_source with correct paper_id."""
    record = _make_paper_record(paper_id="paper-uuid-99")
    mock_paper_store = MagicMock()
    mock_paper_store.find_paper.return_value = record
    mock_user_library = MagicMock()

    meta = PaperMetadata(url="https://doi.org/10.1234/test")
    result = acquire_paper_local(
        meta, storage_path="", state=None,
        paper_store=mock_paper_store, user_library=mock_user_library,
    )

    assert result.status == "ok_library_doi"
    mock_user_library.add_source.assert_called_once()
    call_kwargs = mock_user_library.add_source.call_args
    assert call_kwargs[0][0] == "paper-uuid-99"  # paper_id positional


def test_acquire_doi_dedup_fills_meta_from_library():
    """DOI dedup fills meta fields from library record for citekey generation."""
    record = _make_paper_record(title="Library Paper", authors="Jones, Alice", year=2021)
    mock_paper_store = MagicMock()
    mock_paper_store.find_paper.return_value = record

    # meta has no title/authors — should be filled from library
    meta = PaperMetadata(url="https://doi.org/10.1234/test")
    result = acquire_paper_local(meta, storage_path="", paper_store=mock_paper_store)

    assert result.status == "ok_library_doi"
    # citekey should include author name and year (Jones2021_...)
    assert "Jones" in result.citekey or "jones" in result.citekey.lower()


def test_acquire_no_doi_no_paper_store_no_dedup():
    """Without DOI or paper_store, dedup is skipped (no crash)."""
    meta = PaperMetadata(url="https://example.com/paper.pdf")
    with patch("klemma.skills.acquirer.download_pdf", return_value=None):
        result = acquire_paper_local(meta, storage_path="")
    # No crash — download failed, no DOI → download_failed
    assert result.status == "download_failed"


# ── Hash dedup (post-download, pre-extraction) ────────────────────────────


def _make_tmp_pdf(tmp_path: Path) -> Path:
    """Create a minimal fake PDF file."""
    p = tmp_path / "test.pdf"
    p.write_bytes(b"%PDF-1.4 fake content for test" + b"\x00" * 100)
    return p


def test_acquire_hash_dedup_skips_metadata_extraction(tmp_path):
    """Hash match: library metadata used instead of calling resolve_metadata.

    We verify this behaviorally: register_paper is NOT called (paper already
    in library), and the result uses library data.
    """
    fake_pdf = _make_tmp_pdf(tmp_path)
    record = _make_paper_record(
        pdf_hash="deadbeef", doi="10.9999/hash-test",
        title="Hash Paper", authors="Green, Alice", year=2019,
    )

    mock_paper_store = MagicMock()
    mock_paper_store.find_paper.side_effect = lambda **kw: (
        None if kw.get("doi") else record  # DOI miss, hash hit
    )

    meta = PaperMetadata(url="https://example.com/paper.pdf")
    with patch("klemma.skills.acquirer.download_pdf", return_value=fake_pdf), \
         patch("klemma.skills.acquirer.compute_pdf_hash", return_value="deadbeef"), \
         patch("klemma.skills.acquirer._try_zotero", return_value=None):
        result = acquire_paper_local(
            meta, storage_path="", paper_store=mock_paper_store,
        )

    assert result.status == "ok"
    # Library data used → register_paper NOT called (already in library)
    mock_paper_store.register_paper.assert_not_called()
    # Library author propagated to citekey
    assert "Green" in result.citekey or "green" in result.citekey.lower()


def test_acquire_hash_dedup_uses_library_metadata(tmp_path):
    """Hash match uses library metadata for citekey generation."""
    fake_pdf = _make_tmp_pdf(tmp_path)
    record = _make_paper_record(
        pdf_hash="deadbeef", title="Hash Match Paper", authors="Brown, Bob", year=2020,
    )

    mock_paper_store = MagicMock()
    mock_paper_store.find_paper.side_effect = lambda **kw: (
        None if kw.get("doi") else record
    )

    meta = PaperMetadata(url="https://example.com/paper.pdf")
    with patch("klemma.skills.acquirer.download_pdf", return_value=fake_pdf), \
         patch("klemma.skills.acquirer.compute_pdf_hash", return_value="deadbeef"), \
         patch("klemma.skills.acquirer._try_zotero", return_value=None):
        result = acquire_paper_local(meta, storage_path="", paper_store=mock_paper_store)

    assert "Brown" in result.citekey or "brown" in result.citekey.lower()


def test_acquire_hash_dedup_registers_citekey_in_user_library(tmp_path):
    """Hash hit: new citekey must be registered in user_library (step 6b)."""
    fake_pdf = _make_tmp_pdf(tmp_path)
    record = _make_paper_record(pdf_hash="existinghash", paper_id="existing-pid")

    mock_paper_store = MagicMock()
    mock_paper_store.find_paper.side_effect = lambda **kw: (
        None if kw.get("doi") else record  # DOI miss, hash hit
    )
    mock_user_library = MagicMock()

    meta = PaperMetadata(url="https://example.com/paper.pdf")
    with patch("klemma.skills.acquirer.download_pdf", return_value=fake_pdf), \
         patch("klemma.skills.acquirer.compute_pdf_hash", return_value="existinghash"), \
         patch("klemma.skills.acquirer._try_zotero", return_value=None):
        result = acquire_paper_local(
            meta, storage_path="",
            paper_store=mock_paper_store, user_library=mock_user_library,
        )

    assert result.status == "ok"
    mock_user_library.add_source.assert_called_once()
    call_args = mock_user_library.add_source.call_args
    assert call_args[0][0] == "existing-pid"   # paper_id
    assert call_args[0][1] == result.citekey    # citekey


# ── Write-through (new paper → library) ───────────────────────────────────


def test_acquire_write_through_registers_paper_in_library(tmp_path):
    """New paper (no dedup hit) is registered in paper_store after acquire."""
    fake_pdf = _make_tmp_pdf(tmp_path)

    mock_paper_store = MagicMock()
    mock_paper_store.find_paper.return_value = None
    mock_paper_store.register_paper.return_value = "new-paper-uuid"
    mock_user_library = MagicMock()

    meta = PaperMetadata(
        url="https://example.com/paper.pdf",
        title="New Paper",
        authors="Taylor, Carl",
        year=2024,
        doi="10.5555/new",
    )
    with patch("klemma.skills.acquirer.download_pdf", return_value=fake_pdf), \
         patch("klemma.skills.acquirer.compute_pdf_hash", return_value="newhash123"), \
         patch("klemma.skills.acquirer.resolve_metadata", return_value={}), \
         patch("klemma.skills.acquirer._try_zotero", return_value=None):
        result = acquire_paper_local(
            meta, storage_path="",
            paper_store=mock_paper_store, user_library=mock_user_library,
        )

    assert result.status == "ok"
    assert result.pdf_hash == "newhash123"
    mock_paper_store.register_paper.assert_called_once()
    call_kwargs = mock_paper_store.register_paper.call_args[1]
    assert call_kwargs["pdf_hash"] == "newhash123"
    assert call_kwargs["doi"] == "10.5555/new"


def test_acquire_write_through_registers_citekey_in_user_library(tmp_path):
    """New paper: citekey registered in user_library after acquire."""
    fake_pdf = _make_tmp_pdf(tmp_path)

    mock_paper_store = MagicMock()
    mock_paper_store.find_paper.return_value = None
    mock_paper_store.register_paper.return_value = "new-paper-uuid"
    mock_user_library = MagicMock()

    meta = PaperMetadata(
        url="https://example.com/paper.pdf",
        title="New Paper", authors="Taylor, Carl", year=2024,
    )
    with patch("klemma.skills.acquirer.download_pdf", return_value=fake_pdf), \
         patch("klemma.skills.acquirer.compute_pdf_hash", return_value="newhash123"), \
         patch("klemma.skills.acquirer.resolve_metadata", return_value={}), \
         patch("klemma.skills.acquirer._try_zotero", return_value=None):
        result = acquire_paper_local(
            meta, storage_path="",
            paper_store=mock_paper_store, user_library=mock_user_library,
        )

    assert result.status == "ok"
    mock_user_library.add_source.assert_called_once()
    call_args = mock_user_library.add_source.call_args
    assert call_args[0][0] == "new-paper-uuid"  # paper_id
    assert call_args[0][1] == result.citekey    # citekey
    assert call_args[1]["status"] == "completed"


def test_acquire_write_through_skipped_on_hash_dedup(tmp_path):
    """Hash dedup hit should NOT call register_paper (already in library)."""
    fake_pdf = _make_tmp_pdf(tmp_path)
    record = _make_paper_record(pdf_hash="existinghash")

    mock_paper_store = MagicMock()
    mock_paper_store.find_paper.side_effect = lambda **kw: (
        None if kw.get("doi") else record
    )

    meta = PaperMetadata(url="https://example.com/paper.pdf")
    with patch("klemma.skills.acquirer.download_pdf", return_value=fake_pdf), \
         patch("klemma.skills.acquirer.compute_pdf_hash", return_value="existinghash"), \
         patch("klemma.skills.acquirer._try_zotero", return_value=None):
        acquire_paper_local(meta, storage_path="", paper_store=mock_paper_store)

    mock_paper_store.register_paper.assert_not_called()


def test_acquire_without_paper_store_unchanged_behavior(tmp_path):
    """Without paper_store, behavior is identical to pre-dedup code."""
    fake_pdf = _make_tmp_pdf(tmp_path)

    meta = PaperMetadata(url="https://example.com/paper.pdf", title="Legacy", year=2020)
    with patch("klemma.skills.acquirer.download_pdf", return_value=fake_pdf), \
         patch("klemma.skills.acquirer.resolve_metadata", return_value={"title": "Legacy"}), \
         patch("klemma.skills.acquirer._try_zotero", return_value=None):
        result = acquire_paper_local(meta, storage_path="")

    assert result.status == "ok"
    assert result.citekey  # some citekey generated
