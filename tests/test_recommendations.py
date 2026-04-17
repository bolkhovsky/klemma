"""Unit tests for klemma.api.recommendations (#331 Child A).

Tests the pure/isolated functions that build the LLM-curated recommendations
pipeline: hashing, language detection, parse_llm_output, loaded-sources
selection, and the compute_scored_gaps equivalence-preservation check.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from klemma.api.recommendations import (
    apply_recency_filter,
    build_prompt_inputs,
    compute_library_state_hash,
    compute_outline_hash,
    compute_scored_gaps,
    detect_rationale_language,
    parse_llm_output,
    select_loaded_sources,
)

# ---------------------------------------------------------------------------
# compute_library_state_hash
# ---------------------------------------------------------------------------


def _src(paper_id: str, status: str = "completed"):
    return SimpleNamespace(paper_id=paper_id, status=status)


def test_compute_library_state_hash_stable_across_order():
    a = [_src("p1"), _src("p2"), _src("p3")]
    b = [_src("p3"), _src("p1"), _src("p2")]
    assert compute_library_state_hash(a) == compute_library_state_hash(b)


def test_compute_library_state_hash_changes_on_source_add():
    before = compute_library_state_hash([_src("p1"), _src("p2")])
    after = compute_library_state_hash([_src("p1"), _src("p2"), _src("p3")])
    assert before != after


def test_compute_library_state_hash_changes_on_status_change():
    before = compute_library_state_hash([_src("p1", "completed")])
    after = compute_library_state_hash([_src("p1", "pending")])
    assert before != after


# ---------------------------------------------------------------------------
# compute_outline_hash
# ---------------------------------------------------------------------------


def test_compute_outline_hash_stable_across_reorder():
    a = [{"id": "1", "name": "Intro"}, {"id": "2", "name": "Methods"}]
    b = [{"id": "2", "name": "Methods"}, {"id": "1", "name": "Intro"}]
    assert compute_outline_hash(a) == compute_outline_hash(b)


def test_compute_outline_hash_changes_on_rename():
    a = [{"id": "1", "name": "Intro"}]
    b = [{"id": "1", "name": "Introduction"}]
    assert compute_outline_hash(a) != compute_outline_hash(b)


def test_compute_outline_hash_changes_on_add_remove():
    a = [{"id": "1", "name": "Intro"}]
    b = [{"id": "1", "name": "Intro"}, {"id": "2", "name": "Methods"}]
    assert compute_outline_hash(a) != compute_outline_hash(b)


def test_compute_outline_hash_accepts_none_and_empty():
    assert compute_outline_hash(None) == compute_outline_hash([])


def test_compute_outline_hash_accepts_pydantic_like():
    a = [SimpleNamespace(id="1", name="Intro")]
    b = [{"id": "1", "name": "Intro"}]
    assert compute_outline_hash(a) == compute_outline_hash(b)


# ---------------------------------------------------------------------------
# detect_rationale_language
# ---------------------------------------------------------------------------


def test_detect_rationale_language_russian_on_cyrillic():
    assert detect_rationale_language("Прогнозирование морского льда") == "Russian"


def test_detect_rationale_language_english_on_latin():
    assert detect_rationale_language("Sea-ice forecasting with deep learning") == "English"


def test_detect_rationale_language_fallback_russian_on_empty():
    assert detect_rationale_language("") == "Russian"
    assert detect_rationale_language(None) == "Russian"


def test_detect_rationale_language_mixed_cyrillic_wins():
    assert detect_rationale_language("Klemma: AI для аспирантов") == "Russian"


# ---------------------------------------------------------------------------
# parse_llm_output
# ---------------------------------------------------------------------------


def test_parse_llm_output_valid_dict():
    raw = {
        "recommendations": [
            {"title": "U-Net", "authors": "Ronneberger", "year": 2015,
             "rationale": "Segmentation baseline", "score": 9.0},
        ]
    }
    out = parse_llm_output(raw)
    assert len(out) == 1
    assert out[0]["title"] == "U-Net"
    assert out[0]["score"] == 9.0


def test_parse_llm_output_valid_json_string():
    raw = '{"recommendations":[{"title":"X","rationale":"R","score":7}]}'
    out = parse_llm_output(raw)
    assert out == [{"title": "X", "authors": "", "year": None, "doi": None,
                    "rationale": "R", "score": 7.0}]


def test_parse_llm_output_invalid_json_returns_empty():
    assert parse_llm_output("not json at all") == []


def test_parse_llm_output_none_returns_empty():
    assert parse_llm_output(None) == []


def test_parse_llm_output_missing_recommendations_key():
    assert parse_llm_output({"other": []}) == []


def test_parse_llm_output_drops_items_without_title():
    raw = {"recommendations": [
        {"title": "OK", "rationale": "", "score": 5},
        {"title": "", "rationale": "skip", "score": 5},
        {"rationale": "no title"},
    ]}
    out = parse_llm_output(raw)
    assert len(out) == 1
    assert out[0]["title"] == "OK"


def test_parse_llm_output_clamps_score_above():
    raw = {"recommendations": [{"title": "T", "score": 99}]}
    assert parse_llm_output(raw)[0]["score"] == 10.0


def test_parse_llm_output_clamps_score_below():
    raw = {"recommendations": [{"title": "T", "score": -5}]}
    assert parse_llm_output(raw)[0]["score"] == 1.0


def test_parse_llm_output_handles_string_year():
    raw = {"recommendations": [{"title": "T", "year": "2023"}]}
    assert parse_llm_output(raw)[0]["year"] == 2023


def test_parse_llm_output_invalid_year_becomes_none():
    raw = {"recommendations": [{"title": "T", "year": "not-a-year"}]}
    assert parse_llm_output(raw)[0]["year"] is None


# ---------------------------------------------------------------------------
# build_prompt_inputs
# ---------------------------------------------------------------------------


def test_build_prompt_inputs_handles_empty_outline():
    ctx = build_prompt_inputs(
        project_name="Topic",
        outline=[],
        loaded_sources=[],
        candidates=[],
        rationale_language="Russian",
    )
    assert ctx["project_name"] == "Topic"
    assert "не задан" in ctx["outline_md"]
    assert "нет обработанных" in ctx["loaded_sources_md"]
    assert "кандидатов нет" in ctx["candidates_md"]
    assert ctx["rationale_language"] == "Russian"


def test_build_prompt_inputs_renders_candidates():
    candidates = [
        {"title": "Cand A", "authors": "Author A", "year": 2020,
         "cited_by_count": 3, "top_intent": "method", "doi": "10.1/a"},
        {"title": "Cand B", "authors": "Author B", "year": 2021,
         "cited_by_count": 1, "top_intent": None},
    ]
    ctx = build_prompt_inputs(
        project_name="T", outline=[{"id": "1", "name": "Intro"}],
        loaded_sources=[], candidates=candidates, rationale_language="English",
    )
    assert "Cand A" in ctx["candidates_md"]
    assert "cited_by=3" in ctx["candidates_md"]
    assert "Cand B" in ctx["candidates_md"]
    assert "Intro" in ctx["outline_md"]


def test_build_prompt_inputs_truncates_preview_in_md():
    long_preview = "x" * 5000
    loaded = [{"title": "Paper", "authors": "A", "year": 2020,
               "preview": long_preview}]
    ctx = build_prompt_inputs(
        project_name="T", outline=[], loaded_sources=loaded,
        candidates=[], rationale_language="Russian",
    )
    # Markdown rendering itself doesn't truncate; length cap is in select_loaded_sources
    assert "Paper" in ctx["loaded_sources_md"]


# ---------------------------------------------------------------------------
# apply_recency_filter
# ---------------------------------------------------------------------------


def test_apply_recency_filter_drops_old_low_cite():
    scored = [
        {"title": "new", "year": 2024, "cited_by_count": 1},
        {"title": "old-obscure", "year": 2005, "cited_by_count": 1},
        {"title": "old-classic", "year": 2005, "cited_by_count": 5},
    ]
    out = apply_recency_filter(scored, today_year=2026)
    titles = {g["title"] for g in out}
    assert "new" in titles
    assert "old-classic" in titles
    assert "old-obscure" not in titles


def test_apply_recency_filter_keeps_year_none():
    scored = [{"title": "no-year", "year": None, "cited_by_count": 0}]
    out = apply_recency_filter(scored, today_year=2026)
    assert out == scored


# ---------------------------------------------------------------------------
# select_loaded_sources
# ---------------------------------------------------------------------------


def _paper(paper_id: str, title: str, abstract: str = "", authors: str = "",
           year: int | None = None, doi: str | None = None):
    return SimpleNamespace(
        paper_id=paper_id, title=title, authors=authors, year=year,
        abstract=abstract, doi=doi,
    )


def test_select_loaded_sources_prefers_abstract_over_raw_text():
    paper_store = MagicMock()
    paper_store.get_paper_by_id.side_effect = lambda pid: _paper(pid, "T", abstract="ABSTRACT")
    paper_store.get_raw_text.return_value = "RAWTEXT_VERY_LONG" * 100

    library = MagicMock()
    library.get_all_sources.return_value = [
        SimpleNamespace(paper_id="p1", citekey="ck1", status="completed", added_at=""),
    ]

    out = select_loaded_sources(
        paper_store=paper_store, library=library, user_id="u1", max_items=5,
    )
    assert len(out) == 1
    assert out[0]["preview"] == "ABSTRACT"
    paper_store.get_raw_text.assert_not_called()


def test_select_loaded_sources_uses_raw_text_when_abstract_empty():
    paper_store = MagicMock()
    paper_store.get_paper_by_id.side_effect = lambda pid: _paper(pid, "T", abstract="")
    paper_store.get_raw_text.return_value = "rawtext-content"

    library = MagicMock()
    library.get_all_sources.return_value = [
        SimpleNamespace(paper_id="p1", citekey="ck1", status="completed", added_at=""),
    ]

    out = select_loaded_sources(
        paper_store=paper_store, library=library, user_id="u1", max_items=5,
    )
    assert out[0]["preview"] == "rawtext-content"


def test_select_loaded_sources_skips_non_completed():
    paper_store = MagicMock()
    paper_store.get_paper_by_id.side_effect = lambda pid: _paper(pid, "T", abstract="a")

    library = MagicMock()
    library.get_all_sources.return_value = [
        SimpleNamespace(paper_id="p1", citekey="ck1", status="pending", added_at=""),
        SimpleNamespace(paper_id="p2", citekey="ck2", status="failed", added_at=""),
        SimpleNamespace(paper_id="p3", citekey="ck3", status="completed", added_at=""),
    ]

    out = select_loaded_sources(
        paper_store=paper_store, library=library, user_id="u1", max_items=5,
    )
    assert len(out) == 1
    assert out[0]["paper_id"] == "p3"


def test_select_loaded_sources_sorts_by_preview_length_desc():
    paper_store = MagicMock()
    abstracts = {"p1": "short", "p2": "a" * 2000, "p3": "medium-length text"}
    paper_store.get_paper_by_id.side_effect = lambda pid: _paper(pid, f"T-{pid}", abstract=abstracts[pid])

    library = MagicMock()
    library.get_all_sources.return_value = [
        SimpleNamespace(paper_id="p1", citekey="ck1", status="completed", added_at="2026-01-01"),
        SimpleNamespace(paper_id="p2", citekey="ck2", status="completed", added_at="2026-01-02"),
        SimpleNamespace(paper_id="p3", citekey="ck3", status="completed", added_at="2026-01-03"),
    ]

    out = select_loaded_sources(
        paper_store=paper_store, library=library, user_id="u1", max_items=5,
    )
    # p2 has the longest (truncated to 1500) abstract → first
    assert out[0]["paper_id"] == "p2"


def test_select_loaded_sources_returns_at_most_max_items():
    paper_store = MagicMock()
    paper_store.get_paper_by_id.side_effect = lambda pid: _paper(pid, "T", abstract="a")

    library = MagicMock()
    library.get_all_sources.return_value = [
        SimpleNamespace(paper_id=f"p{i}", citekey=f"ck{i}", status="completed", added_at="")
        for i in range(10)
    ]

    out = select_loaded_sources(
        paper_store=paper_store, library=library, user_id="u1", max_items=3,
    )
    assert len(out) == 3


def test_select_loaded_sources_skips_missing_paper():
    paper_store = MagicMock()
    paper_store.get_paper_by_id.return_value = None

    library = MagicMock()
    library.get_all_sources.return_value = [
        SimpleNamespace(paper_id="p1", citekey="ck1", status="completed", added_at=""),
    ]

    out = select_loaded_sources(
        paper_store=paper_store, library=library, user_id="u1", max_items=5,
    )
    assert out == []


def test_select_loaded_sources_truncates_preview_to_1500():
    paper_store = MagicMock()
    long_abstract = "z" * 5000
    paper_store.get_paper_by_id.side_effect = lambda pid: _paper(pid, "T", abstract=long_abstract)

    library = MagicMock()
    library.get_all_sources.return_value = [
        SimpleNamespace(paper_id="p1", citekey="ck1", status="completed", added_at=""),
    ]

    out = select_loaded_sources(
        paper_store=paper_store, library=library, user_id="u1", max_items=5,
    )
    assert len(out[0]["preview"]) == 1500


# ---------------------------------------------------------------------------
# compute_scored_gaps — empty/edge cases
# ---------------------------------------------------------------------------


def test_compute_scored_gaps_empty_user_sources_returns_empty():
    paper_store = MagicMock()
    library = MagicMock()
    library.get_all_sources.return_value = []
    project_store = MagicMock()

    out = compute_scored_gaps(
        paper_store=paper_store, library=library, project_store=project_store,
        user_id="u1", limit=50,
    )
    assert out == []
    paper_store.get_reference_gaps.assert_not_called()


def test_compute_scored_gaps_empty_raw_gaps_returns_empty():
    paper_store = MagicMock()
    paper_store.get_reference_gaps.return_value = ([], {})
    paper_store.get_paper_embeddings_batch.return_value = {}

    library = MagicMock()
    library.get_all_sources.return_value = [_src("p1"), _src("p2"), _src("p3")]
    library.get_citekey_map.return_value = {}

    project_store = MagicMock()
    project_store.get_source_sections_bulk.return_value = {}
    project_store.get_section_centroids.return_value = {}

    out = compute_scored_gaps(
        paper_store=paper_store, library=library, project_store=project_store,
        user_id="u1", limit=50,
    )
    assert out == []
