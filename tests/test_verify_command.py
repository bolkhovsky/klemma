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
    kctx.config.ai.verify_citations_inline = True
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
    try:
        runner = CliRunner(mix_stderr=False)
    except TypeError:
        runner = CliRunner()
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


def test_claude_backend_judge_runs_without_api_key(tmp_path, monkeypatch):
    """backend=claude: the CLI judge is built (no degraded mode) and its verdicts land in --json."""
    import json
    import re

    from klemma import ai as ai_mod
    from klemma.ai import AICallResult
    from klemma.config import AIConfig

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(ai_mod.ClaudeClient, "check_cli_available", staticmethod(lambda: True))

    def fake_call(self, system, user, **kw):
        ids = re.findall(r"\d+:\d+", system)
        verdicts = [{
            "anchor_id": i, "verdict": "ok", "contradiction": False, "severity": "ok",
            "offending_span": "", "reason": "supported",
        } for i in dict.fromkeys(ids)]
        return AICallResult(
            text=json.dumps({"verdicts": verdicts}), duration_ms=1,
            input_tokens=10, output_tokens=5, model="claude-sonnet-5",
        )

    monkeypatch.setattr(ai_mod.ClaudeClient, "call_with_meta", fake_call)

    md = tmp_path / "chapter.md"
    md.write_text(
        "# Chapter\n\nThe ice edge is defined as the 15 % concentration contour @smith2020.\n",
        encoding="utf-8",
    )
    sidecar_dir = tmp_path / ".klemma" / "pdfs"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "smith2020.md").write_text(
        "---\ncitekey: smith2020\n---\n\n[Page 1]\nThe ice edge is the contour of 15 % "
        "sea ice concentration in the analysis.\n",
        encoding="utf-8",
    )

    kctx = _make_kctx(tmp_path)
    kctx.config.ai = AIConfig(
        backend="claude", model="sonnet", citation_check_max_wall_clock=1800,
    )
    result = _invoke(["--json", str(md)], kctx=kctx, tmp_path=tmp_path)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output[result.output.find("["):])
    report = data[0]
    assert report["status"] != "degraded"
    assert report["model"] == "claude-sonnet-5"
    assert any(v["ai_used"] for v in report["verdicts"])
    numeric = next(v for v in report["verdicts"] if v["anchor_kind"] == "numeric")
    assert numeric["evidence_locator"]
    assert numeric["evidence_span"] and len(numeric["evidence_span"]) == 2


def test_status_line_goes_to_stderr(capsys):
    """The per-command status line must not pollute --json stdout."""
    from klemma.cli import _print_status_line

    state = MagicMock()
    state.get_stats.return_value = {"total": 3}
    state.get_fragment_stats.return_value = {"total": 7}
    state.get_gap_summary.return_value = {"open_count": 0}
    state.get_prune_summary.return_value = {"total": 0}
    _print_status_line(state, project_name="proj", model="sonnet")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "3 sources" in captured.err


def test_missing_vault_path_is_a_clear_error(tmp_path, monkeypatch):
    """A vault path from another machine gives a ClickException, not a traceback."""
    (tmp_path / ".klemma").mkdir()
    (tmp_path / ".klemma" / "config.yaml").write_text(
        "obsidian:\n  vault_path: /nonexistent-klemma-test/vault\n  notes_folder: Refs\n",
        encoding="utf-8",
    )
    (tmp_path / "a.md").write_text("See [@x2020].\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(klemma_cli, ["check-citations", "--no-ai", "a.md"])
    assert result.exit_code == 1
    assert "obsidian.vault_path" in result.output
    assert not isinstance(result.exception, OSError)
