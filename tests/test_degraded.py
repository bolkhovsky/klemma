"""Tests for the `degraded` source status — silent pipeline failures made loud.

A source whose embeddings (or sidecar) silently failed must not sit in
`klemma status` as 'completed': `_process_single` collects the failures and
marks the source `degraded`, `klemma repair` fixes it back to `completed`,
and `klemma repair --scan` backfills the flag for historical sources.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from klemma.cli import _process_single
from klemma.cli import main as klemma_cli
from klemma.literature.models import ExtractionResult, Fragment
from klemma.literature.sidecar import write_pdf_sidecar
from klemma.repositories.sources import ProcessingStatus as RepoStatus
from klemma.state import ProcessingStatus as EnumStatus
from klemma.state import StateManager

PAGES = ["First page prose. " * 10, "Second page prose. " * 8]


class TestStatusDefinitions:
    def test_degraded_in_both_status_vocabularies(self) -> None:
        assert EnumStatus.DEGRADED.value == "degraded"
        assert RepoStatus.DEGRADED == "degraded"
        assert "degraded" in RepoStatus.ALL

    def test_mark_clear_roundtrip(self, tmp_path: Path) -> None:
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["a2020"])
        state.mark_degraded("a2020", ["embeddings", "sidecar", "embeddings"])

        src = state.get_source("a2020")
        assert src["status"] == "degraded"
        assert json.loads(src["degraded_steps"]) == ["embeddings", "sidecar"]

        rows = state.get_degraded_sources()
        assert rows == [{"id": "a2020", "degraded_steps": ["embeddings", "sidecar"]}]

        state.clear_degraded("a2020")
        src = state.get_source("a2020")
        assert src["status"] == "completed"
        assert src["degraded_steps"] is None
        assert state.get_degraded_sources() == []

    def test_clear_degraded_noop_on_other_statuses(self, tmp_path: Path) -> None:
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["a2020"])  # status=pending
        state.clear_degraded("a2020")
        assert state.get_source("a2020")["status"] == "pending"

    def test_get_stats_counts_degraded(self, tmp_path: Path) -> None:
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["a2020", "b2021"])
        state.mark_degraded("a2020", ["embeddings"])
        stats = state.get_stats()
        assert stats["degraded"] == 1
        assert stats["total"] == 2


def _run_process_single(citekey, state, tmp_path, *, embeddings):
    """Drive _process_single through the full path with a scripted extractor."""
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

    extraction = ExtractionResult(
        source_id=citekey,
        fragments=[Fragment(text="First page prose.", verbatim=False, page=1)],
    )

    with patch("klemma.skills.extractor.extract_fragments", return_value=extraction), \
         patch("klemma.literature.metadata.lookup_s2", return_value=None):
        return _process_single(
            citekey=citekey,
            cfg=cfg,
            state=state,
            vault=MagicMock(),
            ai=MagicMock(),
            pdf_extractor=pdf_extractor,
            library=library,
            quiet=True,
            klemma_home=tmp_path / ".klemma",
            embeddings=embeddings,
        )


class TestProcessDegrades:
    def test_embed_backend_failure_marks_degraded(self, tmp_path: Path, capsys):
        """Embedding backend down → completed is overridden by degraded."""
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["alice2021"])
        # Fragment already in DB so the auto-embed loop has work to fail on.
        state.save_fragments(
            "alice2021", [{"text": "First page prose.", "page": 1}]
        )

        embeddings = MagicMock()
        embeddings.model_name = "test-embed"
        embeddings.embed.side_effect = ConnectionError("Ollama is down")

        n, status = _run_process_single(
            "alice2021", state, tmp_path, embeddings=embeddings
        )
        assert (n, status) == (1, "ok")

        src = state.get_source("alice2021")
        assert src["status"] == "degraded"
        assert json.loads(src["degraded_steps"]) == ["embeddings"]

        # The failure is visible in the console, not only in the logger.
        out = capsys.readouterr().out
        assert "degraded" in out
        assert "embeddings" in out

    def test_healthy_embed_backend_stays_completed(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["bob2022"])
        state.save_fragments("bob2022", [{"text": "First page prose.", "page": 1}])

        embeddings = MagicMock()
        embeddings.model_name = "test-embed"
        embeddings.embed.return_value = [0.1, 0.2, 0.3]

        n, status = _run_process_single(
            "bob2022", state, tmp_path, embeddings=embeddings
        )
        assert (n, status) == (1, "ok")
        assert state.get_source("bob2022")["status"] != "degraded"


def _make_repair_kctx(tmp_path: Path, state: StateManager, embeddings=None):
    kctx = MagicMock()
    kctx.state = state
    kctx.project_root = tmp_path
    kctx.klemma_home = tmp_path / ".klemma"
    kctx.config.ai.max_pdf_chars = 50000
    kctx.config.zotero.storage_path = str(tmp_path / "storage")
    kctx.library = None
    kctx.paper_store = None
    kctx.user_library = None
    kctx.embeddings = embeddings
    return kctx


def _invoke_repair(args, kctx):
    runner = CliRunner()
    with patch("klemma.commands.repair._get_context", return_value=kctx):
        return runner.invoke(klemma_cli, ["repair"] + args, catch_exceptions=False)


class TestRepairEmbeddings:
    def _seed_degraded(self, tmp_path: Path) -> StateManager:
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["alice2021"])
        state.save_fragments(
            "alice2021", [{"text": "First page prose.", "page": 1}]
        )
        state.mark_degraded("alice2021", ["embeddings"])
        return state

    def test_repair_returns_source_to_completed(self, tmp_path: Path):
        state = self._seed_degraded(tmp_path)
        embeddings = MagicMock()
        embeddings.model_name = "test-embed"
        embeddings.embed.return_value = [0.1, 0.2, 0.3]
        kctx = _make_repair_kctx(tmp_path, state, embeddings=embeddings)

        result = _invoke_repair(["alice2021", "--steps", "embeddings"], kctx)
        assert result.exit_code == 0, result.output

        src = state.get_source("alice2021")
        assert src["status"] == "completed"
        assert src["degraded_steps"] is None
        frags = state.get_fragments(source_id="alice2021", limit=10)
        assert all(f["embedding"] for f in frags)
        assert "degraded → completed" in result.output

    def test_repair_keeps_degraded_when_backend_still_down(self, tmp_path: Path):
        state = self._seed_degraded(tmp_path)
        embeddings = MagicMock()
        embeddings.model_name = "test-embed"
        embeddings.embed.side_effect = ConnectionError("still down")
        kctx = _make_repair_kctx(tmp_path, state, embeddings=embeddings)

        result = _invoke_repair(["alice2021", "--steps", "embeddings"], kctx)
        assert result.exit_code == 0, result.output
        assert state.get_source("alice2021")["status"] == "degraded"

    def test_repair_without_backend_warns_and_skips(self, tmp_path: Path):
        state = self._seed_degraded(tmp_path)
        kctx = _make_repair_kctx(tmp_path, state, embeddings=None)

        result = _invoke_repair(["alice2021", "--steps", "embeddings"], kctx)
        assert result.exit_code == 0, result.output
        assert "no embeddings backend" in result.output
        assert state.get_source("alice2021")["status"] == "degraded"


class TestStatusDegradedFlag:
    def _invoke_status(self, args, kctx):
        runner = CliRunner()
        with patch("klemma.commands.analyze._get_context", return_value=kctx), \
             patch("klemma.commands.analyze._sync_sections"):
            return runner.invoke(klemma_cli, ["status"] + args, catch_exceptions=False)

    def test_degraded_list(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["alice2021"])
        state.mark_degraded("alice2021", ["embeddings", "sidecar"])
        kctx = _make_repair_kctx(tmp_path, state)

        result = self._invoke_status(["--degraded"], kctx)
        assert result.exit_code == 0, result.output
        assert "alice2021" in result.output
        assert "embeddings" in result.output
        assert "sidecar" in result.output

    def test_degraded_list_empty(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        kctx = _make_repair_kctx(tmp_path, state)
        result = self._invoke_status(["--degraded"], kctx)
        assert result.exit_code == 0, result.output
        assert "No degraded sources" in result.output

    def test_summary_counts_degraded(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["alice2021", "bob2022"])
        state.mark_degraded("alice2021", ["embeddings"])
        kctx = _make_repair_kctx(tmp_path, state)
        kctx.project = None
        kctx.project_store = None
        kctx.config.dissertation.min_sources_per_section = 3

        result = self._invoke_status([], kctx)
        assert result.exit_code == 0, result.output
        assert "1 degraded" in result.output


class TestScan:
    def test_scan_finds_unembedded_and_missing_sidecar(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["novec2020", "nosidecar2021"])
        # Completed source with sidecar but fragments without vectors.
        state.save_fragments("novec2020", [{"text": "fragment text", "page": 1}])
        state.update_source_metadata("novec2020", status="completed")
        write_pdf_sidecar(tmp_path, "novec2020", ["page text"], {})
        # Completed source without any sidecar at all.
        state.update_source_metadata("nosidecar2021", status="completed")

        embeddings = MagicMock()
        embeddings.model_name = "test-embed"
        kctx = _make_repair_kctx(tmp_path, state, embeddings=embeddings)

        result = _invoke_repair(["--scan"], kctx)
        assert result.exit_code == 0, result.output

        novec = state.get_source("novec2020")
        assert novec["status"] == "degraded"
        assert json.loads(novec["degraded_steps"]) == ["embeddings"]

        nosidecar = state.get_source("nosidecar2021")
        assert nosidecar["status"] == "degraded"
        assert "sidecar" in json.loads(nosidecar["degraded_steps"])

    def test_scan_dry_run_only_reports(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["novec2020"])
        state.save_fragments("novec2020", [{"text": "fragment text", "page": 1}])
        state.update_source_metadata("novec2020", status="completed")
        write_pdf_sidecar(tmp_path, "novec2020", ["page text"], {})

        embeddings = MagicMock()
        embeddings.model_name = "test-embed"
        kctx = _make_repair_kctx(tmp_path, state, embeddings=embeddings)

        result = _invoke_repair(["--scan", "--dry-run"], kctx)
        assert result.exit_code == 0, result.output
        assert "novec2020" in result.output
        assert state.get_source("novec2020")["status"] == "completed"

    def test_scan_without_backend_checks_sidecar_only(self, tmp_path: Path):
        state = StateManager(tmp_path / "test.db")
        state.register_sources(["novec2020"])
        state.save_fragments("novec2020", [{"text": "fragment text", "page": 1}])
        state.update_source_metadata("novec2020", status="completed")
        write_pdf_sidecar(tmp_path, "novec2020", ["page text"], {})

        kctx = _make_repair_kctx(tmp_path, state, embeddings=None)
        result = _invoke_repair(["--scan"], kctx)
        assert result.exit_code == 0, result.output
        # Sidecar present + no embeddings criterion → stays completed.
        assert state.get_source("novec2020")["status"] == "completed"
        assert "only sidecar presence" in result.output
