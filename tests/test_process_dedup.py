"""Tests for citekey-based dedup fast path in _process_single() (#162).

Verifies that when a citekey is already in user_library (processed in another
project), _process_single() reuses library fragments WITHOUT reading the PDF.
"""

from unittest.mock import MagicMock

from klemma.cli import _process_single
from klemma.models import FragmentRecord
from klemma.state import StateManager


def _make_frag(fid="f1", paper_id="pid1", text="Key finding."):
    return FragmentRecord(
        fragment_id=fid,
        paper_id=paper_id,
        fragment_text=text,
        fragment_type="key_idea",
        page_number=1,
        citation_intent="background",
        content_hash=fid,
    )


def _make_call(citekey, state, *, user_library=None, paper_store=None, force=False):
    """Invoke _process_single with all side-effectful deps mocked.

    Returns ((n, status), pdf_extractor) so callers can assert on pdf_extractor.
    pdf_extractor.find_pdf is set to return None by default — the fast path
    tests don't reach find_pdf at all; fall-through tests get 'PDF not found'.
    """
    cfg = MagicMock()
    cfg.ai.max_pdf_chars = 50000
    cfg.ai.model = "test-model"
    cfg.zotero.storage_path = "/tmp/storage"
    cfg.processing.min_pdf_length = 100

    vault = MagicMock()
    ai = MagicMock()

    pdf_extractor = MagicMock()
    pdf_extractor.find_pdf.return_value = None  # causes "PDF not found" in fall-throughs

    library = MagicMock()
    library.entries.get.return_value = None
    library.pdf_paths = {}

    result = _process_single(
        citekey=citekey,
        cfg=cfg,
        state=state,
        vault=vault,
        ai=ai,
        pdf_extractor=pdf_extractor,
        library=library,
        quiet=True,
        user_library=user_library,
        paper_store=paper_store,
        force=force,
    )
    return result, pdf_extractor


class TestCitekeyFastPathDedup:
    """_process_single() citekey-based dedup fast path."""

    def test_reuses_fragments_without_pdf_read(self, tmp_path):
        """Citekey in user_library with fragments → reuse, PDF never searched."""
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["alice2021"])

        frag = _make_frag()
        user_library = MagicMock()
        user_library.resolve_paper_id.return_value = "paper_id_A"
        paper_store = MagicMock()
        paper_store.get_fragments.return_value = [frag]

        (n, status), pdf_extractor = _make_call(
            "alice2021", state, user_library=user_library, paper_store=paper_store
        )

        assert n == 1
        assert status == "ok"
        pdf_extractor.find_pdf.assert_not_called()
        user_library.resolve_paper_id.assert_called_once_with("alice2021")
        paper_store.get_fragments.assert_called_once_with("paper_id_A")

    def test_fragments_saved_to_project_state(self, tmp_path):
        """Fast-path fragments are saved into the project state.db."""
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["alice2021"])

        frag = _make_frag(fid="f1", text="Unique library fragment text.")
        user_library = MagicMock()
        user_library.resolve_paper_id.return_value = "pid1"
        paper_store = MagicMock()
        paper_store.get_fragments.return_value = [frag]

        _make_call("alice2021", state, user_library=user_library, paper_store=paper_store)

        fragments = state.get_fragments(source_id="alice2021")
        assert len(fragments) == 1
        assert fragments[0]["fragment_text"] == "Unique library fragment text."

    def test_falls_through_when_no_fragments_in_library(self, tmp_path):
        """Citekey in user_library but NO fragments → fall through (PDF path attempted)."""
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["alice2021"])

        user_library = MagicMock()
        user_library.resolve_paper_id.return_value = "paper_id_A"
        paper_store = MagicMock()
        paper_store.get_fragments.return_value = []  # registered but not processed

        (n, status), pdf_extractor = _make_call(
            "alice2021", state, user_library=user_library, paper_store=paper_store
        )

        pdf_extractor.find_pdf.assert_called()  # fell through to PDF path
        assert status == "PDF not found"  # find_pdf returns None → early exit

    def test_falls_through_when_citekey_not_in_library(self, tmp_path):
        """Citekey NOT in user_library → fall through, get_fragments not called."""
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["alice2021"])

        user_library = MagicMock()
        user_library.resolve_paper_id.return_value = None
        paper_store = MagicMock()

        (n, status), pdf_extractor = _make_call(
            "alice2021", state, user_library=user_library, paper_store=paper_store
        )

        pdf_extractor.find_pdf.assert_called()
        paper_store.get_fragments.assert_not_called()

    def test_force_bypasses_citekey_check(self, tmp_path):
        """--force flag skips citekey dedup even when library has the source."""
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["alice2021"])

        user_library = MagicMock()
        user_library.resolve_paper_id.return_value = "paper_id_A"
        paper_store = MagicMock()
        paper_store.get_fragments.return_value = [_make_frag()]

        (n, status), pdf_extractor = _make_call(
            "alice2021", state, user_library=user_library, paper_store=paper_store, force=True
        )

        # Force bypasses fast path → PDF search attempted
        pdf_extractor.find_pdf.assert_called()
        user_library.resolve_paper_id.assert_not_called()

    def test_no_user_library_falls_through(self, tmp_path):
        """user_library=None → fast path inactive, falls through to PDF check."""
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["alice2021"])

        paper_store = MagicMock()

        (n, status), pdf_extractor = _make_call(
            "alice2021", state, user_library=None, paper_store=paper_store
        )

        pdf_extractor.find_pdf.assert_called()
        paper_store.get_fragments.assert_not_called()

    def test_multiple_fragments_all_saved(self, tmp_path):
        """All library fragments are copied to project state on fast path."""
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["jones2020"])

        frags = [
            _make_frag("f1", "pid1", "Fragment one."),
            _make_frag("f2", "pid1", "Fragment two."),
            _make_frag("f3", "pid1", "Fragment three."),
        ]
        user_library = MagicMock()
        user_library.resolve_paper_id.return_value = "pid1"
        paper_store = MagicMock()
        paper_store.get_fragments.return_value = frags

        (n, status), pdf_extractor = _make_call(
            "jones2020", state, user_library=user_library, paper_store=paper_store
        )

        assert n == 3
        assert status == "ok"
        pdf_extractor.find_pdf.assert_not_called()
        saved = state.get_fragments(source_id="jones2020")
        assert len(saved) == 3
