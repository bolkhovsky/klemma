"""Tests for `klemma repair` — sidecar backfill + honest verbatim recompute."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.literature.sidecar import load_sidecar_doc, write_pdf_sidecar
from klemma.state import StateManager
from klemma.text_normalize import normalize

GOST_PAGE = (
    "3.4 Определение требуемой обеспеченности\n"
    "Определение требуемой обеспеченности и эффективности метода на основе "
    "оперативных (независимых) данных.\n"
)
SECOND_PAGE = "Прочие положения стандарта, не относящиеся к делу.\n"


def _make_kctx(tmp_path: Path, state: StateManager):
    kctx = MagicMock()
    kctx.state = state
    kctx.project_root = tmp_path
    kctx.klemma_home = tmp_path / ".klemma"
    kctx.config.ai.max_pdf_chars = 50000
    kctx.config.zotero.storage_path = str(tmp_path / "storage")
    kctx.library = None
    kctx.paper_store = None
    kctx.user_library = None
    return kctx


def _invoke(args, kctx):
    runner = CliRunner()
    with patch("klemma.commands.repair._get_context", return_value=kctx):
        return runner.invoke(klemma_cli, ["repair"] + args, catch_exceptions=False)


def _provenance_dump(state: StateManager, citekey: str) -> list[tuple]:
    rows = state.get_fragments(source_id=citekey, limit=1000)
    return sorted(
        (
            r["fragment_text"],
            r["verbatim"],
            r["char_start"],
            r["char_end"],
            r["source_locator"],
        )
        for r in rows
    )


class TestSidecarBackfill:
    def test_backfill_from_cited_manuscript(self, tmp_path: Path):
        """--cited scans [@citekey] refs; only cited sources get sidecars."""
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["smith2020", "ivanov2021"])

        manuscript = tmp_path / "draft.md"
        manuscript.write_text(
            "Норма установлена в [@smith2020], см. также [-@smith2020].\n",
            encoding="utf-8",
        )

        mock_extractor = MagicMock()
        mock_extractor.find_pdf.return_value = tmp_path / "paper.pdf"
        mock_extractor.extract_pages.return_value = [GOST_PAGE, SECOND_PAGE]

        kctx = _make_kctx(tmp_path, state)
        with patch(
            "klemma.literature.pdf.PDFExtractor", return_value=mock_extractor
        ):
            result = _invoke(
                ["--cited", str(manuscript), "--steps", "sidecar"], kctx
            )

        assert result.exit_code == 0, result.output
        assert (tmp_path / ".klemma" / "pdfs" / "smith2020.md").exists()
        assert not (tmp_path / ".klemma" / "pdfs" / "ivanov2021.md").exists()
        src = state.get_source("smith2020")
        assert src["pdf_text_length"] == len(GOST_PAGE) + len(SECOND_PAGE)

    def test_existing_sidecar_not_rewritten(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["smith2020"])
        path = write_pdf_sidecar(tmp_path, "smith2020", [GOST_PAGE], {})
        before = path.read_text(encoding="utf-8")

        mock_extractor = MagicMock()
        kctx = _make_kctx(tmp_path, state)
        with patch(
            "klemma.literature.pdf.PDFExtractor", return_value=mock_extractor
        ):
            result = _invoke(["smith2020", "--steps", "sidecar"], kctx)

        assert result.exit_code == 0, result.output
        assert path.read_text(encoding="utf-8") == before
        mock_extractor.find_pdf.assert_not_called()

    def test_missing_pdf_reported(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["ghost2020"])

        mock_extractor = MagicMock()
        mock_extractor.find_pdf.return_value = None
        kctx = _make_kctx(tmp_path, state)
        with patch(
            "klemma.literature.pdf.PDFExtractor", return_value=mock_extractor
        ):
            result = _invoke(["ghost2020", "--steps", "sidecar"], kctx)

        assert result.exit_code == 0, result.output
        assert "PDF not found" in result.output
        assert not (tmp_path / ".klemma" / "pdfs" / "ghost2020.md").exists()


class TestVerbatimRecompute:
    def _seed(self, tmp_path: Path) -> StateManager:
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["gost2025"])
        write_pdf_sidecar(tmp_path, "gost2025", [GOST_PAGE, SECOND_PAGE], {})
        state.save_fragments(
            "gost2025",
            [
                # Real quote stored as paraphrase → must upgrade to verbatim.
                {
                    "text": "Определение требуемой обеспеченности и эффективности метода",
                    "verbatim": False,
                    "page": 1,
                },
                # Fabricated quote stored as verbatim → must downgrade.
                {
                    "text": "Transformers achieve state-of-the-art on ImageNet",
                    "verbatim": True,
                    "page": 2,
                },
            ],
        )
        return state

    def test_flip_both_directions_with_spans(self, tmp_path: Path):
        state = self._seed(tmp_path)
        kctx = _make_kctx(tmp_path, state)

        result = _invoke(["--steps", "verbatim"], kctx)
        assert result.exit_code == 0, result.output

        rows = {
            r["fragment_text"]: r
            for r in state.get_fragments(source_id="gost2025", limit=100)
        }
        real = rows["Определение требуемой обеспеченности и эффективности метода"]
        fake = rows["Transformers achieve state-of-the-art on ImageNet"]

        assert real["verbatim"] == 1
        assert real["char_start"] is not None and real["char_end"] is not None
        doc = load_sidecar_doc(tmp_path, "gost2025")
        assert normalize(doc.text[real["char_start"]:real["char_end"]]) == normalize(
            real["fragment_text"]
        )
        assert real["source_locator"] == "п. 3.4"

        assert fake["verbatim"] == 0
        assert fake["char_start"] is None
        assert fake["char_end"] is None
        assert fake["source_locator"] is None

    def test_idempotent_second_run(self, tmp_path: Path):
        state = self._seed(tmp_path)
        kctx = _make_kctx(tmp_path, state)

        first = _invoke(["--steps", "verbatim"], kctx)
        assert first.exit_code == 0, first.output
        dump_after_first = _provenance_dump(state, "gost2025")

        second = _invoke(["--steps", "verbatim"], kctx)
        assert second.exit_code == 0, second.output
        assert _provenance_dump(state, "gost2025") == dump_after_first
        # Nothing flips on the rerun — flags already honest.
        assert "(0↑)" in second.output
        assert "0 downgraded" in second.output

    def test_dry_run_writes_nothing(self, tmp_path: Path):
        state = self._seed(tmp_path)
        kctx = _make_kctx(tmp_path, state)
        before = _provenance_dump(state, "gost2025")

        result = _invoke(["--steps", "verbatim", "--dry-run"], kctx)
        assert result.exit_code == 0, result.output
        assert _provenance_dump(state, "gost2025") == before
        assert "Dry run" in result.output

    def test_no_sidecar_skips_source(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["nosidecar2020"])
        state.save_fragments(
            "nosidecar2020", [{"text": "какой-то фрагмент", "verbatim": True}]
        )
        kctx = _make_kctx(tmp_path, state)

        result = _invoke(["--steps", "verbatim"], kctx)
        assert result.exit_code == 0, result.output
        assert "no sidecar" in result.output
        # Flag untouched — nothing to validate against.
        rows = state.get_fragments(source_id="nosidecar2020", limit=10)
        assert rows[0]["verbatim"] == 1

    def test_dual_write_to_paper_store(self, tmp_path: Path):
        state = self._seed(tmp_path)
        kctx = _make_kctx(tmp_path, state)
        kctx.paper_store = MagicMock()
        kctx.user_library = MagicMock()
        kctx.user_library.resolve_paper_id.return_value = "paper-uuid-1"

        result = _invoke(["--steps", "verbatim"], kctx)
        assert result.exit_code == 0, result.output

        flags = [
            call.args[1]
            for call in kctx.paper_store.update_fragment_verbatim.call_args_list
        ]
        assert sorted(flags) == [False, True]  # downgrade + upgrade dual-written


class TestCliSurface:
    def test_unknown_step_rejected(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        kctx = _make_kctx(tmp_path, state)
        result = _invoke(["--steps", "teleport"], kctx)
        assert result.exit_code == 1
        assert "Unknown step" in result.output

    def test_unknown_citekey_warned(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["real2020"])
        write_pdf_sidecar(tmp_path, "real2020", [GOST_PAGE], {})
        kctx = _make_kctx(tmp_path, state)

        result = _invoke(["missing2020", "--steps", "verbatim"], kctx)
        assert result.exit_code == 0, result.output
        assert "missing2020" in result.output
        assert "not in project DB" in result.output

    def test_backfill_verbatim_alias_deprecated(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["gost2025"])
        write_pdf_sidecar(tmp_path, "gost2025", [GOST_PAGE], {})
        state.save_fragments(
            "gost2025",
            [{
                "text": "Определение требуемой обеспеченности и эффективности метода",
                "verbatim": False,
                "page": 1,
            }],
        )
        kctx = _make_kctx(tmp_path, state)

        runner = CliRunner()
        with patch("klemma.commands.manage._get_context", return_value=kctx):
            result = runner.invoke(
                klemma_cli, ["backfill-verbatim"], catch_exceptions=False
            )

        assert result.exit_code == 0, result.output
        assert "deprecated" in result.output
        rows = state.get_fragments(source_id="gost2025", limit=10)
        assert rows[0]["verbatim"] == 1  # repair engine did the flip
