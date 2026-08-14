"""Tests for klemma find-source — reverse source lookup (claim-provenance PR-6)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from klemma.cli import main as klemma_cli
from klemma.state import StateManager


class _FakeEmbeddings:
    """Deterministic embeddings provider stub."""

    dim = 3

    def __init__(self, model_name="test-model", vec=(1.0, 0.0, 0.0)):
        self.model_name = model_name
        self._vec = list(vec)
        self.embed_calls = 0

    def embed(self, title, abstract=""):
        self.embed_calls += 1
        return list(self._vec)


def _make_kctx(project_root: Path, state, embeddings):
    kctx = MagicMock()
    kctx.state = state
    kctx.embeddings = embeddings
    kctx.project_root = project_root
    return kctx


def _invoke(args, kctx):
    try:
        runner = CliRunner(mix_stderr=False)
    except TypeError:
        runner = CliRunner()
    with patch("klemma.commands.find._get_context", return_value=kctx):
        return runner.invoke(klemma_cli, ["find-source"] + args, catch_exceptions=False)


def _parse_json(output: str) -> dict:
    start = output.find("{")
    assert start != -1, f"no JSON in output: {output!r}"
    return json.loads(output[start:])


def _seed_corpus(state: StateManager, model="test-model"):
    """Two sources: ren2025 matches the query vector, park2024 does not."""
    state.register_sources(["ren2025", "park2024"])

    state.save_fragments("ren2025", [{
        "text": "Предложена NIIEE loss, учитывающая ошибку кромки льда при обучении U-Net.",
        "type": "key_idea", "section": "2.1", "relevance": 5, "page": 3,
    }])
    state.save_fragments("park2024", [{
        "text": "IIEE используется как метрика оценки качества прогноза кромки.",
        "type": "key_idea", "section": "2.1", "relevance": 4, "page": 7,
    }])

    frag_ids = {f["source_id"]: f["id"] for f in state.get_fragments()}
    state.save_fragment_embedding(frag_ids["ren2025"], [1.0, 0.0, 0.0], model)
    state.save_fragment_embedding(frag_ids["park2024"], [0.0, 1.0, 0.0], model)
    state.update_fragment_provenance(
        frag_ids["ren2025"], verbatim=True, char_start=100, char_end=180,
        source_locator="п. 3.4",
    )

    state.save_embedding("ren2025", [0.9, 0.1, 0.0], model)
    state.save_embedding("park2024", [0.0, 0.9, 0.1], model)
    return frag_ids


# ---------------------------------------------------------------------------
# Ranked output
# ---------------------------------------------------------------------------


def test_ranked_output_with_locator_and_verbatim(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    _seed_corpus(state)
    kctx = _make_kctx(tmp_path, state, _FakeEmbeddings())

    result = _invoke(["Функции потерь для U-Net учитывают ошибку кромки", "--json"], kctx)
    assert result.exit_code == 0, result.output

    data = _parse_json(result.output)
    assert data["model"] == "test-model"
    candidates = data["candidates"]
    assert [c["citekey"] for c in candidates][:2] == ["ren2025", "park2024"]

    best = candidates[0]
    assert best["similarity"] > candidates[1]["similarity"]
    assert best["locator"] == "п. 3.4"
    assert best["verbatim"] is True
    assert "NIIEE" in best["preview"]


def test_table_output_shows_verbatim_mark(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    _seed_corpus(state)
    kctx = _make_kctx(tmp_path, state, _FakeEmbeddings())

    result = _invoke(["Функции потерь учитывают ошибку кромки"], kctx)
    assert result.exit_code == 0, result.output
    assert "@ren2025" in result.output
    assert "verbatim" in result.output
    assert "пересказ" in result.output  # park2024's fragment is a paraphrase
    assert "п. 3.4" in result.output


def test_page_fallback_when_no_locator(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    _seed_corpus(state)
    kctx = _make_kctx(tmp_path, state, _FakeEmbeddings(vec=(0.0, 1.0, 0.0)))

    result = _invoke(["Метрика оценки кромки", "--json"], kctx)
    data = _parse_json(result.output)
    best = data["candidates"][0]
    assert best["citekey"] == "park2024"
    assert best["locator"] == "с. 7"  # no source_locator → page number
    assert best["verbatim"] is False


def test_top_k_limits_candidates(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    _seed_corpus(state)
    kctx = _make_kctx(tmp_path, state, _FakeEmbeddings())

    result = _invoke(["ошибка кромки", "-k", "1", "--json"], kctx)
    data = _parse_json(result.output)
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["citekey"] == "ren2025"


def test_source_only_hit_without_fragments(tmp_path):
    """A source embedded at source level but without fragments still surfaces."""
    state = StateManager(tmp_path / "klemma.db")
    state.register_sources(["yang2025"])
    state.save_embedding("yang2025", [1.0, 0.0, 0.0], "test-model")
    kctx = _make_kctx(tmp_path, state, _FakeEmbeddings())

    result = _invoke(["ошибка кромки", "--json"], kctx)
    data = _parse_json(result.output)
    assert data["candidates"][0]["citekey"] == "yang2025"
    assert data["candidates"][0]["preview"] is None


# ---------------------------------------------------------------------------
# Coverage warnings
# ---------------------------------------------------------------------------


def test_model_mismatch_warns_with_other_models(tmp_path):
    """Corpus embedded with a different model → visible warning, no crash."""
    state = StateManager(tmp_path / "klemma.db")
    _seed_corpus(state, model="old-model")
    kctx = _make_kctx(tmp_path, state, _FakeEmbeddings(model_name="new-model"))

    result = _invoke(["ошибка кромки"], kctx)
    assert result.exit_code == 0, result.output
    assert "old-model" in result.output
    assert "new-model" in result.output
    assert "--remodel" in result.output


def test_partial_coverage_prints_counts(tmp_path):
    """Half-embedded corpus → coverage warning with numbers, results still shown."""
    state = StateManager(tmp_path / "klemma.db")
    _seed_corpus(state)
    # A third source with a fragment nobody embedded
    state.register_sources(["gap2026"])
    state.save_fragments("gap2026", [{
        "text": "Совсем не эмбеддированный фрагмент про другую тему.",
        "type": "key_idea", "section": "1.1", "relevance": 3,
    }])
    kctx = _make_kctx(tmp_path, state, _FakeEmbeddings())

    result = _invoke(["ошибка кромки"], kctx)
    assert result.exit_code == 0, result.output
    assert "Покрытие" in result.output
    assert "2/3" in result.output  # fragments: 2 of 3 embedded with active model
    assert "@ren2025" in result.output


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


def test_graceful_without_provider(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    kctx = _make_kctx(tmp_path, state, None)

    result = _invoke(["любое утверждение"], kctx)
    assert result.exit_code == 0
    assert "не настроены" in result.output


def test_graceful_empty_corpus(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    kctx = _make_kctx(tmp_path, state, _FakeEmbeddings())

    result = _invoke(["любое утверждение"], kctx)
    assert result.exit_code == 0
    assert "нет эмбеддингов" in result.output


def test_query_embed_failure_exits_2(tmp_path):
    state = StateManager(tmp_path / "klemma.db")
    _seed_corpus(state)
    emb = _FakeEmbeddings()
    emb.embed = MagicMock(side_effect=RuntimeError("backend down"))
    kctx = _make_kctx(tmp_path, state, emb)

    result = _invoke(["любое утверждение"], kctx)
    assert result.exit_code == 2
    assert "backend down" in result.output
