"""Tests for the sidecar-first write in `_process_single()`.

The raw PDF sidecar and `sources.pdf_text_length` must be persisted right
after successful text extraction — BEFORE the AI call — so the full text
survives an AI failure or a zero-fragment extraction (claim-provenance
substrate, docs/plans/2026-08-13-claim-provenance-gap.md).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from klemma.cli import _process_single
from klemma.state import StateManager

PAGES = ["First page prose. " * 10, "Second page prose. " * 8]


def _make_call(citekey, state, tmp_path, *, extract_side_effect=None):
    """Invoke _process_single with a found PDF and a scripted AI extractor.

    `extract_side_effect` is wired into `extract_fragments` (return_value
    when it's not an exception). Returns (result, sidecar_path).
    """
    cfg = MagicMock()
    cfg.ai.max_pdf_chars = 50000
    cfg.ai.model = "test-model"
    cfg.zotero.storage_path = str(tmp_path / "storage")
    cfg.processing.min_pdf_length = 100

    pdf_extractor = MagicMock()
    pdf_extractor.find_pdf.return_value = tmp_path / "paper.pdf"
    pdf_extractor.extract_pages.return_value = PAGES
    pdf_extractor.format_for_ai.return_value = "\n".join(PAGES)

    library = MagicMock()
    library.entries.get.return_value = None
    library.pdf_paths = {}

    klemma_home = tmp_path / ".klemma"

    with patch("klemma.skills.extractor.extract_fragments") as mock_extract:
        if isinstance(extract_side_effect, Exception):
            mock_extract.side_effect = extract_side_effect
        else:
            mock_extract.return_value = extract_side_effect
        result = _process_single(
            citekey=citekey,
            cfg=cfg,
            state=state,
            vault=MagicMock(),
            ai=MagicMock(),
            pdf_extractor=pdf_extractor,
            library=library,
            quiet=True,
            klemma_home=klemma_home,
        )

    return result, tmp_path / ".klemma" / "pdfs" / f"{citekey}.md"


class TestSidecarBeforeAI:
    """Sidecar + pdf_text_length written even when the AI produces nothing."""

    def test_sidecar_written_on_zero_fragments(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["alice2021"])

        (n, status), sidecar = _make_call(
            "alice2021", state, tmp_path, extract_side_effect=None
        )

        assert (n, status) == (0, "no fragments")
        assert sidecar.exists()
        text = sidecar.read_text(encoding="utf-8")
        assert "First page prose." in text
        assert "<!-- Page 2 -->" in text

    def test_pdf_text_length_recorded(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["alice2021"])

        _make_call("alice2021", state, tmp_path, extract_side_effect=None)

        src = state.get_source("alice2021")
        assert src["pdf_text_length"] == sum(len(p) for p in PAGES)
        # Zero fragments → source is skipped, but the full text survived
        assert src["status"] == "skipped"

    def test_sidecar_survives_ai_crash(self, tmp_path: Path):
        """AI raising mid-extraction must not lose the already-written sidecar."""
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["bob2022"])

        with pytest.raises(RuntimeError, match="AI exploded"):
            _make_call(
                "bob2022", state, tmp_path,
                extract_side_effect=RuntimeError("AI exploded"),
            )

        sidecar = tmp_path / ".klemma" / "pdfs" / "bob2022.md"
        assert sidecar.exists()
        assert state.get_source("bob2022")["pdf_text_length"] == sum(
            len(p) for p in PAGES
        )

    def test_no_sidecar_when_text_too_short(self, tmp_path: Path):
        """Failed extraction (text below threshold) writes nothing."""
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["carol2023"])

        cfg = MagicMock()
        cfg.ai.max_pdf_chars = 50000
        cfg.ai.model = "test-model"
        cfg.zotero.storage_path = str(tmp_path / "storage")
        cfg.processing.min_pdf_length = 100

        pdf_extractor = MagicMock()
        pdf_extractor.find_pdf.return_value = tmp_path / "paper.pdf"
        pdf_extractor.extract_pages.return_value = ["tiny"]
        pdf_extractor.format_for_ai.return_value = "tiny"

        library = MagicMock()
        library.entries.get.return_value = None
        library.pdf_paths = {}

        n, status = _process_single(
            citekey="carol2023",
            cfg=cfg,
            state=state,
            vault=MagicMock(),
            ai=MagicMock(),
            pdf_extractor=pdf_extractor,
            library=library,
            quiet=True,
            klemma_home=tmp_path / ".klemma",
        )

        assert status == "text too short"
        assert not (tmp_path / ".klemma" / "pdfs" / "carol2023.md").exists()
        assert state.get_source("carol2023")["pdf_text_length"] is None


def test_partial_force_renders_merged_corpus_to_vault(tmp_path: Path):
    """Codex P1 on PR-A: after a partial --force the vault note shows the merged
    stored corpus, not the partial new subset."""
    from klemma.literature.models import ExtractionResult, Fragment

    state = MagicMock()
    state.get_source.return_value = {"source_type": "", "pdf_path": None}
    state.get_fragments.return_value = [
        {"fragment_text": "old kept", "fragment_type": "quote", "relevance_score": 4},
        {"fragment_text": "new one", "fragment_type": "quote", "relevance_score": 3},
    ]
    cfg = MagicMock()
    cfg.ai.max_pdf_chars = 50000
    cfg.ai.model = "test-model"
    cfg.zotero.storage_path = str(tmp_path / "storage")
    cfg.processing.min_pdf_length = 10
    pdf_extractor = MagicMock()
    pdf_extractor.find_pdf.return_value = tmp_path / "paper.pdf"
    pdf_extractor.extract_pages.return_value = PAGES
    pdf_extractor.format_for_ai.return_value = "\n".join(PAGES)
    library = MagicMock()
    library.entries.get.return_value = None
    library.pdf_paths = {}
    partial = ExtractionResult(
        source_id="k", fragments=[Fragment(text="new one")], chunk_total=2, failed_chunks=1,
        coverage_ratio=0.5,
    )
    with (
        patch("klemma.skills.extractor.extract_fragments", return_value=partial) as mock_extract,
        patch("klemma.skills.extractor.save_fragments_to_vault", return_value=None) as mock_vault,
        patch("klemma.literature.metadata.lookup_s2", return_value=None),
    ):
        _process_single(
            citekey="k", cfg=cfg, state=state, vault=MagicMock(), ai=MagicMock(),
            pdf_extractor=pdf_extractor, library=library, quiet=True, force=True,
        )
    assert mock_extract.call_args.kwargs["replace_existing"] is True
    texts = [f.text for f in mock_vault.call_args.args[1]]
    assert texts == ["old kept", "new one"]
