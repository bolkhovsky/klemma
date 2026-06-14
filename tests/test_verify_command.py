"""Tests for klemma check-citations CLI command."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from klemma.cli import main as klemma_cli


def _make_kctx(project_root: Path, klemma_home: Path | None = None):
    kctx = MagicMock()
    kctx.config = MagicMock()
    kctx.config.ai.citation_check_model = None
    kctx.config.ai.citation_check_timeout = 60
    kctx.config.ai.citation_check_retries = 0
    kctx.config.ai.citation_check_max_wall_clock = 120
    kctx.config.ai.max_ai_calls_per_draft = 12
    kctx.config.ai.citation_check_max_claim_chars = 1000
    kctx.config.ai.citation_check_max_passage_chars = 2000
    kctx.config.ai.citation_check_max_passages = 8
    kctx.config.ai.citation_check_max_prompt_chars = 12000
    kctx.config.ai.citation_check_max_output_tokens = 1024
    kctx.config.ai.backend = "litellm"
    kctx.config.ai.model = "openai/gpt-4o-mini"
    kctx.config.ai._resolved_api_keys = {}
    kctx.state = MagicMock()
    kctx.project_root = project_root
    kctx.klemma_home = klemma_home or (project_root / ".klemma")
    kctx.project_chain = [project_root]
    kctx.paper_store = None
    kctx.user_library = None
    return kctx


def _invoke(args, kctx=None, tmp_path=None):
    runner = CliRunner(mix_stderr=False)
    tmp = tmp_path or Path("/tmp")
    ctx = kctx or _make_kctx(tmp)

    with patch("klemma.commands.verify._get_context", return_value=ctx):
        result = runner.invoke(klemma_cli, ["check-citations"] + args, catch_exceptions=False)
    return result


# ---------------------------------------------------------------------------
# No-AI mode: deterministic only
# ---------------------------------------------------------------------------

def test_no_targets_no_draft_dir_exits_0(tmp_path):
    result = _invoke(["--no-ai"], kctx=_make_kctx(tmp_path), tmp_path=tmp_path)
    assert result.exit_code == 0


def test_no_ai_simple_file(tmp_path):
    md = tmp_path / "chapter.md"
    md.write_text("# Chapter\n\nSee [@smith2020].\n", encoding="utf-8")

    result = _invoke(["--no-ai", str(md)], kctx=_make_kctx(tmp_path), tmp_path=tmp_path)
    # Should run without errors (source not available → unverifiable)
    assert result.exit_code == 0, result.output


def test_no_ai_hard_warn_exits_1(tmp_path):
    md = tmp_path / "chapter.md"
    # Long verbatim quote that won't be in source → hard_warn (with sidecar available)
    quote = "«очень длинная специфическая цитата которой нет в источнике данного текста»"
    md.write_text(f"# Chapter\n\nАвторы пишут {quote} согласно [@smith2020].\n", encoding="utf-8")

    sidecar_dir = tmp_path / ".klemma" / "pdfs"
    sidecar_dir.mkdir(parents=True)
    sidecar = sidecar_dir / "smith2020.md"
    sidecar.write_text("---\ncitekey: smith2020\n---\n\nSome other content entirely.\n", encoding="utf-8")

    kctx = _make_kctx(tmp_path)
    result = _invoke(["--no-ai", str(md)], kctx=kctx, tmp_path=tmp_path)
    # hard_warn is the default fail-on → exit 1
    assert result.exit_code == 1, result.output


def test_fail_on_never_always_exits_0(tmp_path):
    md = tmp_path / "chapter.md"
    quote = "«очень длинная специфическая цитата которой нет в источнике данного текста»"
    md.write_text(f"# Chapter\n\nАвторы пишут {quote} согласно [@smith2020].\n", encoding="utf-8")

    sidecar_dir = tmp_path / ".klemma" / "pdfs"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "smith2020.md").write_text("---\ncitekey: smith2020\n---\n\nOther content.\n")

    kctx = _make_kctx(tmp_path)
    result = _invoke(["--no-ai", "--fail-on", "never", str(md)], kctx=kctx, tmp_path=tmp_path)
    assert result.exit_code == 0


def test_strict_flag_same_as_fail_on_soft_warn(tmp_path):
    """--strict should be equivalent to --fail-on soft_warn."""
    md = tmp_path / "chapter.md"
    md.write_text("# Chapter\n\nSee [@smith2020].\n", encoding="utf-8")
    kctx = _make_kctx(tmp_path)
    result = _invoke(["--no-ai", "--strict", str(md)], kctx=kctx, tmp_path=tmp_path)
    # No anchors → no verdicts → exit 0 regardless
    assert result.exit_code == 0


def test_json_output_format(tmp_path):
    md = tmp_path / "chapter.md"
    md.write_text("# Chapter\n\nSee [@alpha2021].\n", encoding="utf-8")
    kctx = _make_kctx(tmp_path)
    result = _invoke(["--no-ai", "--json", str(md)], kctx=kctx, tmp_path=tmp_path)
    assert result.exit_code == 0
    import json
    # Skip any CLI banner printed before the JSON
    output = result.output
    json_start = output.find("[")
    assert json_start != -1, f"No JSON found in output: {output!r}"
    data = json.loads(output[json_start:])
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["target"].endswith("chapter.md")
    assert "verdicts" in data[0]


def test_recursive_scans_subdirectory(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    md = sub / "chapter.md"
    md.write_text("# Chapter\n\nSee [@sub2022].\n", encoding="utf-8")
    kctx = _make_kctx(tmp_path)
    result = _invoke(["--no-ai", "--json", "--recursive", str(tmp_path)], kctx=kctx, tmp_path=tmp_path)
    assert result.exit_code == 0
    import json
    output = result.output
    json_start = output.find("[")
    assert json_start != -1
    data = json.loads(output[json_start:])
    targets = [d["target"] for d in data]
    assert any("chapter.md" in t for t in targets)


def test_nonrecursive_dir_warns(tmp_path):
    kctx = _make_kctx(tmp_path)
    result = _invoke(["--no-ai", str(tmp_path)], kctx=kctx, tmp_path=tmp_path)
    assert "directory" in result.output.lower() or "recursive" in result.output.lower() or result.exit_code == 0


def test_default_draft_dir_fallback(tmp_path):
    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "intro.md").write_text("# Intro\n\nSee [@test2023].\n")
    kctx = _make_kctx(tmp_path)
    result = _invoke(["--no-ai", "--json"], kctx=kctx, tmp_path=tmp_path)
    assert result.exit_code == 0
    import json
    output = result.output
    json_start = output.find("[")
    assert json_start != -1
    data = json.loads(output[json_start:])
    assert any("intro.md" in d["target"] for d in data)


def test_no_ai_flag_skips_judge(tmp_path):
    """With --no-ai, build_judge_provider should never be called."""
    md = tmp_path / "draft.md"
    md.write_text("# Chapter\n\nSee [@smith2020].\n", encoding="utf-8")
    kctx = _make_kctx(tmp_path)

    with patch("klemma.commands.verify.build_judge_provider", return_value=None) as mock_build:
        _invoke(["--no-ai", str(md)], kctx=kctx, tmp_path=tmp_path)
        mock_build.assert_not_called()
