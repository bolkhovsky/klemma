"""Tests for fragment RAG in agent context."""

from unittest.mock import MagicMock


def _make_state(tmp_path):
    from klemma.state import StateManager
    sm = StateManager(tmp_path / "test.db")
    sm.register_sources(["paper1"])
    sm.mark_completed("paper1", note_path="@paper1.md")
    sm.save_fragments("paper1", [
        {"text": "Ice forecast accuracy improved by 15%", "type": "result", "section": "1.1",
         "citation_intent": "result_comparison"},
    ])
    frags = sm.get_fragments(source_id="paper1")
    sm.save_fragment_embedding(frags[0]["id"], [1.0, 0.0, 0.0], "test-model")
    return sm


def test_agent_context_with_fragments(tmp_path):
    """When embeddings + query provided, agent context includes relevant fragments."""
    sm = _make_state(tmp_path)

    mock_emb = MagicMock()
    mock_emb.model_name = "test-model"
    mock_emb.embed.return_value = [1.0, 0.0, 0.0]

    from klemma.config import KlemmaConfig
    from klemma.skills.agent import build_agent_context

    config = KlemmaConfig()

    context = build_agent_context(
        config, sm, MagicMock(),
        embeddings=mock_emb,
        query="ice forecast validation",
    )

    assert "Relevant Fragments" in context
    assert "Ice forecast accuracy improved by 15%" in context
    assert "paper1" in context


def test_agent_context_without_embeddings(tmp_path):
    """Without embeddings, no fragment section in context."""
    sm = _make_state(tmp_path)

    from klemma.config import KlemmaConfig
    from klemma.skills.agent import build_agent_context

    config = KlemmaConfig()

    context = build_agent_context(config, sm, MagicMock())

    assert "Relevant Fragments" not in context


def test_agent_context_no_fragment_embeddings(tmp_path):
    """With embeddings but no fragment embeddings, no fragment section."""
    from klemma.state import StateManager
    sm = StateManager(tmp_path / "test.db")
    sm.register_sources(["paper1"])

    mock_emb = MagicMock()
    mock_emb.model_name = "test-model"
    mock_emb.embed.return_value = [1.0, 0.0, 0.0]

    from klemma.config import KlemmaConfig
    from klemma.skills.agent import build_agent_context

    config = KlemmaConfig()
    context = build_agent_context(
        config, sm, MagicMock(),
        embeddings=mock_emb,
        query="test query",
    )

    assert "Relevant Fragments" not in context


def test_agent_context_embed_failure_graceful(tmp_path):
    """If embedding provider fails, RAG degrades gracefully."""
    sm = _make_state(tmp_path)

    mock_emb = MagicMock()
    mock_emb.model_name = "test-model"
    mock_emb.embed.side_effect = RuntimeError("API down")

    from klemma.config import KlemmaConfig
    from klemma.skills.agent import build_agent_context

    config = KlemmaConfig()
    context = build_agent_context(
        config, sm, MagicMock(),
        embeddings=mock_emb,
        query="test query",
    )

    # Should not crash, just no fragments
    assert "Relevant Fragments" not in context
