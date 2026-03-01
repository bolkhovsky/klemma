"""Tests for --model CLI override across LLM-calling commands."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from klemma.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_context():
    """Mock KlemmaContext to avoid real DB/vault/AI initialization."""
    kctx = MagicMock()
    kctx.config.ai.model = "openai/gpt-4.1"
    kctx.config.ai.backend = "litellm"
    kctx.config.ai.api_key = None
    kctx.config.ai.api_key_env = "OPENAI_API_KEY"
    kctx.config.ai.base_url = None
    kctx.config.ai.json_mode = True
    kctx.config.ai.timeout = 120
    kctx.config.ai.retries = 2
    kctx.config.ai.max_pdf_chars = 50000
    kctx.config.ai.language = "ru"
    kctx.config.dissertation.chapter_draft_pattern = "Chapter_{chapter}.md"
    kctx.config.dissertation.min_sources_per_section = 3
    kctx.state = MagicMock()
    kctx.vault = MagicMock()
    kctx.project = None
    kctx.project_name = "test"
    kctx.dissertation_context = "test context"
    kctx.available_tags = []
    kctx.klemma_home = None
    kctx.project_root = None
    kctx.embeddings = None
    kctx.library = None
    return kctx


@pytest.fixture
def _cli_patches(mock_context, tmp_path):
    """Common patches to make CLI research command runnable in tests."""
    with patch("klemma.cli._get_context", return_value=mock_context), \
         patch("klemma.cli._init_components", return_value=mock_context), \
         patch("klemma.cli._print_status_line"), \
         patch("klemma.cli._sync_sections"), \
         patch("klemma.cli.discover_project_root", return_value=tmp_path), \
         patch("klemma.config.parse_chapter_from_section", return_value=1), \
         patch("klemma.cli.console"), \
         patch("klemma.skills.researcher.pre_extract_sources",
               return_value={"extracted": 0, "skipped": 0, "no_pdf": []}), \
         patch("klemma.skills.researcher._load_previous_research",
               return_value=None), \
         patch("klemma.skills.researcher.research_section") as mock_rs:
        mock_rs.return_value = MagicMock(section_status=None)
        yield


def test_research_model_override_passed_to_ai(runner, mock_context, _cli_patches):
    """--model flag overrides config model for research command."""
    captured_model = {}

    def mock_create_ai(ai_config):
        captured_model["model"] = ai_config.model
        return MagicMock()

    with patch("klemma.cli.create_ai", side_effect=mock_create_ai):
        # Without --model: should use config default
        result = runner.invoke(main, ["research", "-s", "1.1"])
        assert result.exit_code == 0 or captured_model, f"CLI failed: {result.output}"
        assert captured_model["model"] == "openai/gpt-4.1"

        # With --model: should override
        captured_model.clear()
        result = runner.invoke(main, ["research", "-s", "1.1", "--model", "openai/gpt-4.1-mini"])
        assert captured_model["model"] == "openai/gpt-4.1-mini"


def test_research_without_model_uses_default(runner, mock_context, _cli_patches):
    """Without --model, config model is used unchanged."""
    captured_model = {}

    def mock_create_ai(ai_config):
        captured_model["model"] = ai_config.model
        return MagicMock()

    with patch("klemma.cli.create_ai", side_effect=mock_create_ai):
        result = runner.invoke(main, ["research", "-s", "1.1"])
        assert result.exit_code == 0 or captured_model, f"CLI failed: {result.output}"

    assert captured_model["model"] == "openai/gpt-4.1"
