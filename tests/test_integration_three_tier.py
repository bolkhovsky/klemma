"""Integration tests for three-tier library sharing (ADR-014 Phase 1G).

Validates cross-project library.db deduplication end-to-end:
- Project A processes a PDF → fragments dual-written to library.db
- Project B with same library.db → _process_single() returns cached fragments, AI skipped
- Project B embed → fragment embeddings loaded from library cache, API skipped
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from klemma.cli import _auto_embed_after_process, _process_single
from klemma.hashing import compute_content_hash, compute_pdf_hash
from klemma.models import FragmentRecord
from klemma.state import StateManager
from klemma.stores.paper_store import LocalPaperStore
from klemma.stores.user_library import LocalUserLibrary

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_pdf(tmp_path: Path) -> Path:
    """Create a real PDF-like file so compute_pdf_hash() can read it."""
    pdf = tmp_path / "smith2020.pdf"
    pdf.write_bytes(b"%PDF-1.4 " + b"x" * 1024)
    return pdf


def _make_state(db_path: Path, citekey: str = "smith2020") -> StateManager:
    sm = StateManager(db_path)
    sm.register_sources([citekey])
    return sm


def _make_library_pair(lib_db: Path) -> tuple[LocalPaperStore, LocalUserLibrary]:
    return LocalPaperStore(lib_db), LocalUserLibrary(lib_db)


def _make_cfg(tmp_path: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.zotero.storage_path = str(tmp_path / "zotero")
    cfg.ai.max_pdf_chars = 50_000
    cfg.ai.model = "claude"
    cfg.processing.min_pdf_length = 100
    return cfg


def _make_pdf_extractor(pdf_path: Path) -> MagicMock:
    mock = MagicMock()
    mock.find_pdf.return_value = pdf_path
    # `_process_single` calls `extract_pages()` then `format_for_ai()` —
    # mock both so the AI-bound text path works under the fitz-reuse
    # refactor (ADR-016).
    mock.extract_pages.return_value = ["A" * 500]
    mock.format_for_ai.return_value = "A" * 500
    mock.extract.return_value = "A" * 500
    return mock


def _make_library() -> MagicMock:
    lib = MagicMock()
    lib.entries = {}
    lib.pdf_paths = {}
    return lib


def _seed_library(ps: LocalPaperStore, pdf_hash: str, texts: list[str]) -> str:
    """Register paper + save fragments in library; return paper_id."""
    paper_id = ps.register_paper(title="Smith 2020", pdf_hash=pdf_hash)
    frags = [
        FragmentRecord(
            fragment_id=compute_content_hash(paper_id, t, i + 1),
            paper_id=paper_id,
            fragment_text=t,
            fragment_type="key_idea",
            page_number=i + 1,
            citation_intent="method",
            content_hash=compute_content_hash(paper_id, t, i + 1),
        )
        for i, t in enumerate(texts)
    ]
    ps.save_fragments(paper_id, frags, prompt_hash="ph1", ai_model="claude")
    return paper_id


# ---------------------------------------------------------------------------
# Phase 1B: process dedup
# ---------------------------------------------------------------------------


class TestProcessDedup:
    """_process_single() hits library cache; AI extraction skipped."""

    def _run(self, tmp_path, ps, ul, pdf, extra_kwargs=None):
        state = _make_state(tmp_path / "state_b.db")
        cfg = _make_cfg(tmp_path)
        kwargs = dict(
            citekey="smith2020",
            cfg=cfg,
            state=state,
            vault=None,
            ai=MagicMock(),
            pdf_extractor=_make_pdf_extractor(pdf),
            library=_make_library(),
            quiet=True,
            paper_store=ps,
            user_library=ul,
        )
        if extra_kwargs:
            kwargs.update(extra_kwargs)
        with patch("klemma.skills.extractor.extract_fragments") as mock_ai:
            n, status = _process_single(**kwargs)
        return n, status, state, mock_ai

    def test_cache_hit_skips_ai(self, tmp_path):
        """extract_fragments NOT called when library has matching PDF hash."""
        pdf = _make_pdf(tmp_path)
        ps, ul = _make_library_pair(tmp_path / "library.db")
        _seed_library(ps, compute_pdf_hash(pdf), ["Finding A", "Finding B"])

        n, status, _, mock_ai = self._run(tmp_path, ps, ul, pdf)

        assert status == "ok"
        assert n == 2
        mock_ai.assert_not_called()

    def test_cache_hit_fragments_in_state(self, tmp_path):
        """Cached library fragments are stored in project B's state DB."""
        pdf = _make_pdf(tmp_path)
        ps, ul = _make_library_pair(tmp_path / "library.db")
        _seed_library(ps, compute_pdf_hash(pdf), ["Finding A", "Finding B"])

        _, _, state, _ = self._run(tmp_path, ps, ul, pdf)

        frags = state.get_fragments(source_id="smith2020")
        assert len(frags) == 2
        texts = {f["fragment_text"] for f in frags}
        assert texts == {"Finding A", "Finding B"}

    def test_cache_hit_source_marked_completed(self, tmp_path):
        """Source is marked completed in project B after cache hit."""
        pdf = _make_pdf(tmp_path)
        ps, ul = _make_library_pair(tmp_path / "library.db")
        _seed_library(ps, compute_pdf_hash(pdf), ["Finding A"])

        _, _, state, _ = self._run(tmp_path, ps, ul, pdf)

        source = state.get_source("smith2020")
        assert source is not None
        assert source["status"] == "completed"

    def test_cache_miss_calls_ai(self, tmp_path):
        """When library has no matching paper, extract_fragments is called."""
        pdf = _make_pdf(tmp_path)
        ps, ul = _make_library_pair(tmp_path / "library.db")
        # Library is empty — no pre-existing paper

        state = _make_state(tmp_path / "state_b.db")
        cfg = _make_cfg(tmp_path)

        fake_frag = MagicMock()
        fake_frag.text = "AI Fragment"
        fake_frag.type = "key_idea"
        fake_frag.page = 1
        fake_frag.citation_intent = "method"
        fake_result = MagicMock()
        fake_result.fragments = [fake_frag]

        with patch("klemma.skills.extractor.extract_fragments", return_value=fake_result) as mock_ai:
            with patch("klemma.skills.extractor.save_fragments_to_vault", return_value=None):
                with patch("klemma.literature.metadata.lookup_s2", return_value=None):
                    _process_single(
                        citekey="smith2020",
                        cfg=cfg,
                        state=state,
                        vault=MagicMock(),
                        ai=MagicMock(),
                        pdf_extractor=_make_pdf_extractor(pdf),
                        library=_make_library(),
                        quiet=True,
                        paper_store=ps,
                        user_library=ul,
                    )

        mock_ai.assert_called_once()


# ---------------------------------------------------------------------------
# Phase 1B: dual-write
# ---------------------------------------------------------------------------


class TestDualWrite:
    """Novel extraction is written to library.db for future projects to reuse."""

    def _run_novel(self, tmp_path, ps, ul, pdf, frag_text="Novel fragment"):
        state = _make_state(tmp_path / "state_a.db")
        cfg = _make_cfg(tmp_path)

        fake_frag = MagicMock()
        fake_frag.text = frag_text
        fake_frag.type = "key_idea"
        fake_frag.page = 1
        fake_frag.citation_intent = None
        fake_result = MagicMock()
        fake_result.fragments = [fake_frag]

        with patch("klemma.skills.extractor.extract_fragments", return_value=fake_result):
            with patch("klemma.skills.extractor.save_fragments_to_vault", return_value=None):
                with patch("klemma.literature.metadata.lookup_s2", return_value=None):
                    _process_single(
                        citekey="smith2020",
                        cfg=cfg,
                        state=state,
                        vault=MagicMock(),
                        ai=MagicMock(),
                        pdf_extractor=_make_pdf_extractor(pdf),
                        library=_make_library(),
                        quiet=True,
                        paper_store=ps,
                        user_library=ul,
                    )
        return state

    def test_paper_in_library_after_novel_extraction(self, tmp_path):
        """After novel extraction, paper appears in library.db by PDF hash."""
        pdf = _make_pdf(tmp_path)
        ps, ul = _make_library_pair(tmp_path / "library.db")

        self._run_novel(tmp_path, ps, ul, pdf)

        rec = ps.find_paper(pdf_hash=compute_pdf_hash(pdf))
        assert rec is not None

    def test_fragments_in_library_after_novel_extraction(self, tmp_path):
        """After novel extraction, fragments are stored in library.db."""
        pdf = _make_pdf(tmp_path)
        ps, ul = _make_library_pair(tmp_path / "library.db")

        self._run_novel(tmp_path, ps, ul, pdf, frag_text="Unique finding")

        rec = ps.find_paper(pdf_hash=compute_pdf_hash(pdf))
        lib_frags = ps.get_fragments(rec.paper_id)
        assert len(lib_frags) == 1
        assert lib_frags[0].fragment_text == "Unique finding"

    def test_citekey_registered_in_user_library(self, tmp_path):
        """After extraction, citekey is mapped to paper_id in user_library."""
        pdf = _make_pdf(tmp_path)
        ps, ul = _make_library_pair(tmp_path / "library.db")

        self._run_novel(tmp_path, ps, ul, pdf)

        pid = ul.resolve_paper_id("smith2020")
        assert pid is not None


# ---------------------------------------------------------------------------
# Phase 1E: embed dedup
# ---------------------------------------------------------------------------


class TestEmbedDedup:
    """_auto_embed_after_process() reuses library fragment embeddings."""

    def _setup_state_with_frags(self, db_path: Path, text: str, page: int) -> StateManager:
        state = _make_state(db_path)
        state.mark_completed("smith2020", note_path="")
        state.save_fragments("smith2020", [
            {"text": text, "type": "key_idea", "chapter": 1, "section": "1.1",
             "relevance": 3, "page": page},
        ])
        return state

    def _seed_library_embedding(
        self, ps: LocalPaperStore, ul: LocalUserLibrary,
        text: str, page: int, vec: list[float], model: str,
    ) -> str:
        paper_id = ps.register_paper(title="Smith 2020", pdf_hash="embed-test-hash")
        frag_hash = compute_content_hash(paper_id, text, page)
        ps.save_fragments(
            paper_id,
            [FragmentRecord(
                fragment_id=frag_hash,
                paper_id=paper_id,
                fragment_text=text,
                fragment_type="key_idea",
                page_number=page,
                citation_intent=None,
                content_hash=frag_hash,
            )],
            prompt_hash="ph1",
            ai_model="claude",
        )
        ps.save_fragment_embedding(frag_hash, vec, model)
        ul.add_source(paper_id, "smith2020", status="completed")
        return paper_id

    def test_cache_hit_skips_embed_api(self, tmp_path):
        """embeddings.embed() not called when library has cached fragment embedding."""
        ps, ul = _make_library_pair(tmp_path / "library.db")
        cached_vec = [0.1, 0.2, 0.3]
        self._seed_library_embedding(ps, ul, "Fragment text", 1, cached_vec, "specterv2")

        state = self._setup_state_with_frags(tmp_path / "state.db", "Fragment text", 1)
        mock_emb = MagicMock()
        mock_emb.model_name = "specterv2"

        _auto_embed_after_process("smith2020", state, mock_emb, quiet=True,
                                  paper_store=ps, user_library=ul)

        mock_emb.embed.assert_not_called()

    def test_cache_miss_calls_embed_api(self, tmp_path):
        """embeddings.embed() called when library has no cached embedding."""
        ps, ul = _make_library_pair(tmp_path / "library.db")
        # Register paper in user_library but store NO embeddings
        paper_id = ps.register_paper(title="Smith 2020", pdf_hash="embed-miss-hash")
        ul.add_source(paper_id, "smith2020", status="completed")

        state = self._setup_state_with_frags(tmp_path / "state.db", "Fragment text", 1)
        mock_emb = MagicMock()
        mock_emb.model_name = "specterv2"
        mock_emb.embed.return_value = [0.5, 0.6, 0.7]

        _auto_embed_after_process("smith2020", state, mock_emb, quiet=True,
                                  paper_store=ps, user_library=ul)

        mock_emb.embed.assert_called_once()


# ---------------------------------------------------------------------------
# End-to-end: two projects sharing one library.db
# ---------------------------------------------------------------------------


class TestCrossProjectSharing:
    def test_project_a_process_enables_project_b_dedup(self, tmp_path):
        """End-to-end: A processes → library.db written → B deduplicates (no AI)."""
        lib_db = tmp_path / "shared_library.db"
        pdf = _make_pdf(tmp_path)
        cfg = _make_cfg(tmp_path)

        # Project A: library empty, full AI extraction + dual-write
        ps_a, ul_a = _make_library_pair(lib_db)
        state_a = _make_state(tmp_path / "a.db")

        fake_frag = MagicMock()
        fake_frag.text = "Key finding"
        fake_frag.type = "evidence"
        fake_frag.page = 2
        fake_frag.citation_intent = "result_comparison"
        fake_result = MagicMock()
        fake_result.fragments = [fake_frag]

        with patch("klemma.skills.extractor.extract_fragments", return_value=fake_result) as ai_a:
            with patch("klemma.skills.extractor.save_fragments_to_vault", return_value=None):
                with patch("klemma.literature.metadata.lookup_s2", return_value=None):
                    n_a, status_a = _process_single(
                        citekey="smith2020", cfg=cfg, state=state_a, vault=MagicMock(),
                        ai=MagicMock(), pdf_extractor=_make_pdf_extractor(pdf),
                        library=_make_library(), quiet=True,
                        paper_store=ps_a, user_library=ul_a,
                    )
        assert ai_a.call_count == 1
        assert status_a == "ok", status_a
        assert n_a == 1

        # Project B: same library.db → cache hit, AI skipped
        ps_b, ul_b = _make_library_pair(lib_db)
        state_b = _make_state(tmp_path / "b.db")

        with patch("klemma.skills.extractor.extract_fragments") as ai_b:
            n_b, status_b = _process_single(
                citekey="smith2020", cfg=cfg, state=state_b, vault=None,
                ai=MagicMock(), pdf_extractor=_make_pdf_extractor(pdf),
                library=_make_library(), quiet=True,
                paper_store=ps_b, user_library=ul_b,
            )
        ai_b.assert_not_called()
        assert status_b == "ok", status_b
        assert n_b == 1

        # Library has the fragment (dual-write happened in A)
        rec = ps_b.find_paper(pdf_hash=compute_pdf_hash(pdf))
        assert rec is not None
        lib_frags = ps_b.get_fragments(rec.paper_id)
        assert len(lib_frags) == 1
        assert lib_frags[0].fragment_text == "Key finding"

        # Project B's state has the fragment (loaded from library cache)
        frags_b = state_b.get_fragments(source_id="smith2020")
        assert len(frags_b) == 1
        assert frags_b[0]["fragment_text"] == "Key finding"


# ---------------------------------------------------------------------------
# Sidecar integration — regression for DOI case-mismatch bug (ADR-016)
# ---------------------------------------------------------------------------


class TestSidecarIntegration:
    """`write_pdf_sidecar` is driven by `_process_single` with a real
    `ZoteroEntry`; verifies the full roundtrip including the DOI field
    (the Pydantic model stores it as `DOI`, uppercase).
    """

    def test_sidecar_contains_doi_from_zotero_entry(self, tmp_path):
        from klemma.literature.models import Author, ZoteroEntry

        pdf = _make_pdf(tmp_path)
        ps, ul = _make_library_pair(tmp_path / "library.db")
        state = _make_state(tmp_path / "state.db")
        cfg = _make_cfg(tmp_path)

        entry = ZoteroEntry(
            id="smith2020",
            title="Smith on sea ice",
            author=[Author(family="Smith", given="A.")],
            issued={"date-parts": [[2020]]},
            DOI="10.1000/smith.sea-ice",
        )
        library = MagicMock()
        library.entries = {"smith2020": entry}
        library.pdf_paths = {}

        fake_frag = MagicMock()
        fake_frag.text = "Key finding"
        fake_frag.type = "evidence"
        fake_frag.page = 1
        fake_frag.citation_intent = None
        fake_result = MagicMock()
        fake_result.fragments = [fake_frag]

        project_root = tmp_path / "project"
        klemma_home = project_root / ".klemma"
        klemma_home.mkdir(parents=True)

        with patch("klemma.skills.extractor.extract_fragments", return_value=fake_result):
            with patch("klemma.skills.extractor.save_fragments_to_vault", return_value=None):
                with patch("klemma.literature.metadata.lookup_s2", return_value=None):
                    _process_single(
                        citekey="smith2020",
                        cfg=cfg,
                        state=state,
                        vault=MagicMock(),
                        ai=MagicMock(),
                        pdf_extractor=_make_pdf_extractor(pdf),
                        library=library,
                        quiet=True,
                        paper_store=ps,
                        user_library=ul,
                        klemma_home=klemma_home,
                    )

        sidecar = project_root / ".klemma" / "pdfs" / "smith2020.md"
        assert sidecar.exists()
        text = sidecar.read_text(encoding="utf-8")
        assert "> DOI: 10.1000/smith.sea-ice" in text
