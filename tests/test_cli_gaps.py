"""Tests for `klemma gaps <citekey>` citation-graph discovery + group routing."""

from unittest.mock import MagicMock, patch

import click
from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.literature.citation_graph import Candidate, SeedWork
from klemma.state import StateManager

# --- Custom group routing (fix #2) ---------------------------------------


def test_router_dispatches_real_subcommand():
    from klemma.commands.analyze import gaps

    ctx = click.Context(gaps)
    name, _cmd, _args = gaps.resolve_command(ctx, ["suggest", "-n", "5"])
    assert name == "suggest"


def test_router_routes_unknown_token_to_walk_as_citekey():
    from klemma.commands.analyze import gaps

    ctx = click.Context(gaps)
    name, _cmd, args = gaps.resolve_command(ctx, ["goesslingPredictability2016"])
    assert name == "walk"
    assert args == ["goesslingPredictability2016"]


def test_gaps_help_ok():
    result = CliRunner().invoke(klemma_cli, ["gaps", "--help"])
    assert result.exit_code == 0


# --- walk command --------------------------------------------------------


def _ctx_with_state(tmp_path, embeddings):
    db = tmp_path / ".klemma" / "state.db"
    db.parent.mkdir(parents=True)
    sm = StateManager(str(db))
    sm.register_sources(["seed1", "owned1"])
    sm.update_source_info(
        "seed1", title="Seed Paper", authors="A.", year=2016,
        doi="10.1/seed", abstract="seed abstract",
    )
    sm.update_source_info("owned1", title="Owned Neighbour", authors="B.", year=2018, doi="10.2/owned")
    # get_all_sources_metadata returns COMPLETED sources only (fix #4) — without
    # mark_completed the owned sets would be empty and nothing would suppress.
    sm.mark_completed("seed1", "/tmp/seed.pdf")
    sm.mark_completed("owned1", "/tmp/owned.pdf")
    sm.save_embedding("seed1", [1.0, 0.0, 0.0], "test-model")

    ctx = MagicMock()
    ctx.state = sm
    ctx.embeddings = embeddings
    ctx.config = MagicMock()
    ctx.library = None
    return ctx


def test_gaps_walk_suppresses_owned_and_lists_fresh(tmp_path):
    emb = MagicMock()
    emb.model_name = "test-model"
    emb.embed_batch.return_value = [[1.0, 0.0, 0.0]]  # one fresh candidate
    mock_ctx = _ctx_with_state(tmp_path, emb)

    seed_work = SeedWork(openalex_id="WSEED", doi="10.1/seed", title="Seed Paper", referenced_works=["WA"])
    candidates = [
        Candidate("W1", "10.2/owned", "Owned Neighbour", "", 2018, "J. Climate", 10, "B", "ref"),
        Candidate("W2", "10.3/new", "New Relevant Paper", "", 2020, "GRL", 5, "C", "cites"),
    ]

    with (
        patch("klemma.cli.discover_project_root", return_value=str(tmp_path)),
        patch("klemma.cli._init_components", return_value=mock_ctx),
        patch("klemma.commands.analyze._get_context", return_value=mock_ctx),
        patch("klemma.commands.analyze._sync_sections"),
        patch("klemma.literature.citation_graph.fetch_seed_work", return_value=seed_work),
        patch("klemma.literature.citation_graph.fetch_citation_graph", return_value=candidates),
    ):
        result = CliRunner().invoke(klemma_cli, ["gaps", "seed1", "--top", "10"])

    assert result.exit_code == 0, result.output
    assert "10.3/new" in result.output            # fresh candidate shown
    assert "1 already in library" in result.output  # owned1 suppressed by DOI
    # seed used its stored embedding (model matched) — no on-the-fly re-embed
    emb.embed.assert_not_called()


def test_gaps_walk_reembeds_seed_on_model_mismatch(tmp_path):
    emb = MagicMock()
    emb.model_name = "different-model"  # != stored "test-model" → must re-embed
    emb.embed.return_value = [1.0, 0.0, 0.0]
    emb.embed_batch.return_value = [[1.0, 0.0, 0.0]]
    mock_ctx = _ctx_with_state(tmp_path, emb)

    seed_work = SeedWork(openalex_id="WSEED", doi="10.1/seed", title="Seed Paper", referenced_works=[])
    candidates = [Candidate("W2", "10.3/new", "New Relevant Paper", "", 2020, "GRL", 5, "C", "cites")]

    with (
        patch("klemma.cli.discover_project_root", return_value=str(tmp_path)),
        patch("klemma.cli._init_components", return_value=mock_ctx),
        patch("klemma.commands.analyze._get_context", return_value=mock_ctx),
        patch("klemma.commands.analyze._sync_sections"),
        patch("klemma.literature.citation_graph.fetch_seed_work", return_value=seed_work),
        patch("klemma.literature.citation_graph.fetch_citation_graph", return_value=candidates),
    ):
        result = CliRunner().invoke(klemma_cli, ["gaps", "seed1"])

    assert result.exit_code == 0, result.output
    emb.embed.assert_called_once()  # re-embedded because model differed


def test_gaps_walk_requires_embedding_backend(tmp_path):
    mock_ctx = MagicMock()
    mock_ctx.embeddings = None  # no backend
    mock_ctx.state = MagicMock()

    with (
        patch("klemma.cli.discover_project_root", return_value=str(tmp_path)),
        patch("klemma.cli._init_components", return_value=mock_ctx),
        patch("klemma.commands.analyze._get_context", return_value=mock_ctx),
        patch("klemma.commands.analyze._sync_sections"),
        patch("klemma.literature.citation_graph.fetch_seed_work") as mock_seed,
    ):
        result = CliRunner().invoke(klemma_cli, ["gaps", "seed1"])

    assert "No embedding backend configured" in result.output
    mock_seed.assert_not_called()  # bailed before any network call


def test_gaps_walk_unknown_citekey(tmp_path):
    emb = MagicMock()
    emb.model_name = "test-model"
    mock_ctx = _ctx_with_state(tmp_path, emb)

    with (
        patch("klemma.cli.discover_project_root", return_value=str(tmp_path)),
        patch("klemma.cli._init_components", return_value=mock_ctx),
        patch("klemma.commands.analyze._get_context", return_value=mock_ctx),
        patch("klemma.commands.analyze._sync_sections"),
        patch("klemma.literature.citation_graph.fetch_seed_work") as mock_seed,
    ):
        result = CliRunner().invoke(klemma_cli, ["gaps", "nonexistent_key"])

    assert "not found" in result.output
    mock_seed.assert_not_called()
