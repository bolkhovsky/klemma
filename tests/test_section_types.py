"""Tests for semantic section types — enum, inference, resolution, DB migration, queries."""

import pytest

from klemma.config import DissertationConfig, ProjectConfig
from klemma.section_types import (
    SECTION_TYPE_KEYWORDS,
    SectionType,
    infer_section_type,
    resolve_section_identifier,
)
from klemma.state import StateManager


@pytest.fixture
def state(tmp_path):
    db_path = tmp_path / "test.db"
    return StateManager(db_path)


# ── Enum ──────────────────────────────────────────────────────────────────


class TestSectionTypeEnum:
    def test_all_values_are_strings(self):
        for st in SectionType:
            assert isinstance(st.value, str)
            assert st.value == st.value.lower()

    def test_expected_members(self):
        expected = {
            "introduction", "background", "literature_review",
            "theoretical_framework", "methodology", "data_description",
            "experiments", "results", "discussion", "conclusion",
            "appendix", "custom",
        }
        assert {st.value for st in SectionType} == expected

    def test_str_enum_comparison(self):
        assert SectionType.METHODOLOGY == "methodology"
        assert SectionType.INTRODUCTION == "introduction"

    def test_keywords_cover_all_types_except_custom(self):
        for st in SectionType:
            if st == SectionType.CUSTOM:
                continue
            assert st in SECTION_TYPE_KEYWORDS, f"Missing keywords for {st}"


# ── Inference ─────────────────────────────────────────────────────────────


class TestInferSectionType:
    @pytest.mark.parametrize("name,expected", [
        ("Введение", SectionType.INTRODUCTION),
        ("Introduction", SectionType.INTRODUCTION),
        ("Обзор существующих решений", SectionType.LITERATURE_REVIEW),
        ("Literature Review", SectionType.LITERATURE_REVIEW),
        ("Анализ литературы по теме", SectionType.LITERATURE_REVIEW),
        ("Методология исследования", SectionType.METHODOLOGY),
        ("Methodology", SectionType.METHODOLOGY),
        ("Research Methods", SectionType.METHODOLOGY),
        ("Экспериментальная оценка", SectionType.EXPERIMENTS),
        ("Experimental Setup", SectionType.EXPERIMENTS),
        ("Результаты", SectionType.RESULTS),
        ("Results and Discussion", SectionType.RESULTS),
        ("Заключение", SectionType.CONCLUSION),
        ("Conclusion", SectionType.CONCLUSION),
        ("Приложение А", SectionType.APPENDIX),
        ("Теоретические основы", SectionType.THEORETICAL_FRAMEWORK),
        ("Описание данных", SectionType.DATA_DESCRIPTION),
        ("Dataset Description", SectionType.DATA_DESCRIPTION),
    ])
    def test_infer_known_names(self, name, expected):
        assert infer_section_type(name) == expected

    def test_infer_unknown_returns_none(self):
        assert infer_section_type("Random Chapter Title") is None
        assert infer_section_type("Глава без ключевых слов") is None

    def test_infer_empty_returns_none(self):
        assert infer_section_type("") is None
        assert infer_section_type(None) is None

    def test_infer_case_insensitive(self):
        assert infer_section_type("METHODOLOGY") == SectionType.METHODOLOGY
        assert infer_section_type("введение") == SectionType.INTRODUCTION


# ── Resolution ────────────────────────────────────────────────────────────


class TestResolveSectionIdentifier:
    def test_numeric_section(self):
        section, st = resolve_section_identifier("2.3")
        assert section == "2.3"
        assert st is None

    def test_numeric_chapter(self):
        section, st = resolve_section_identifier("3")
        assert section == "3"
        assert st is None

    def test_semantic_type_exact(self):
        section, st = resolve_section_identifier("methodology")
        assert section is None
        assert st == SectionType.METHODOLOGY

    def test_semantic_type_with_config_map(self):
        cfg = ProjectConfig(section_type_map={"3": "methodology", "2": "literature_review"})
        section, st = resolve_section_identifier("methodology", cfg)
        assert section == "3"
        assert st == SectionType.METHODOLOGY

    def test_keyword_inference(self):
        section, st = resolve_section_identifier("обзор")
        assert st == SectionType.LITERATURE_REVIEW
        assert section is None

    def test_keyword_with_config_map(self):
        cfg = ProjectConfig(section_type_map={"2": "literature_review"})
        section, st = resolve_section_identifier("обзор", cfg)
        assert section == "2"
        assert st == SectionType.LITERATURE_REVIEW

    def test_unrecognized_falls_back_to_literal(self):
        section, st = resolve_section_identifier("custom-section")
        assert section == "custom-section"
        assert st is None

    def test_empty_input(self):
        assert resolve_section_identifier("") == (None, None)


# ── Config ────────────────────────────────────────────────────────────────


class TestConfigSectionTypeMap:
    def test_from_dissertation_auto_infers(self):
        d = DissertationConfig(
            title="Test",
            chapters={
                1: "Введение",
                2: "Обзор существующих решений",
                3: "Методология",
                4: "Экспериментальная оценка",
                5: "Заключение",
            },
        )
        p = ProjectConfig.from_dissertation(d)
        assert p.section_type_map == {
            "1": "introduction",
            "2": "literature_review",
            "3": "methodology",
            "4": "experiments",
            "5": "conclusion",
        }

    def test_from_dissertation_no_chapters(self):
        d = DissertationConfig(title="Empty")
        p = ProjectConfig.from_dissertation(d)
        assert p.section_type_map == {}

    def test_from_dissertation_partial_inference(self):
        d = DissertationConfig(
            title="Test",
            chapters={1: "Введение", 2: "Что-то непонятное"},
        )
        p = ProjectConfig.from_dissertation(d)
        assert p.section_type_map == {"1": "introduction"}

    def test_explicit_section_type_map_in_config(self):
        p = ProjectConfig(
            section_type_map={"2.1": "methodology", "3": "experiments"},
        )
        assert p.section_type_map["2.1"] == "methodology"

    def test_section_type_weights(self):
        p = ProjectConfig(
            section_type_weights={"methodology": 1.0, "appendix": 0.3},
        )
        assert p.section_type_weights["methodology"] == 1.0


# ── DB migration v7 ──────────────────────────────────────────────────────


class TestMigrationV7:
    def test_schema_version_is_7(self, state):
        import sqlite3
        conn = sqlite3.connect(state.db_path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == 7

    def test_section_type_columns_exist(self, state):
        import sqlite3
        conn = sqlite3.connect(state.db_path)
        for table in ("source_sections", "fragments", "reference_gaps"):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert "section_type" in cols, f"Missing section_type in {table}"
        conn.close()

    def test_section_type_map_table_exists(self, state):
        import sqlite3
        conn = sqlite3.connect(state.db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(section_type_map)")]
        conn.close()
        assert set(cols) == {"section", "section_type", "chapter"}


# ── Sync ──────────────────────────────────────────────────────────────────


class TestSyncSectionTypes:
    def test_sync_populates_map_and_backfills(self, state):
        state.register_sources(["src-a"])
        state.mark_completed("src-a", "/notes/a.md", quality_score=4)
        state.set_source_sections("src-a", ["2.1", "2.3"], [2])
        state.save_fragments("src-a", [
            {"text": "Fragment", "type": "key_idea", "section": "2.1", "relevance": 4},
        ])

        cfg = ProjectConfig(chapters={2: "Обзор литературы", 3: "Методология"})
        result = state.sync_section_types(cfg)

        assert result["updated"] >= 2  # at least source_sections + fragments
        assert result["unmapped"] == []

    def test_sync_with_explicit_map(self, state):
        state.register_sources(["src-b"])
        state.mark_completed("src-b", "/notes/b.md")
        state.set_source_sections("src-b", ["5.1"], [5])

        cfg = ProjectConfig(section_type_map={"5": "discussion"})
        state.sync_section_types(cfg)

        import sqlite3
        conn = sqlite3.connect(state.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT section_type FROM source_sections WHERE source_id='src-b'"
        ).fetchall()
        conn.close()
        assert all(r["section_type"] == "discussion" for r in rows)

    def test_sync_idempotent(self, state):
        """Second sync doesn't re-update already-typed rows."""
        state.register_sources(["src-c"])
        state.mark_completed("src-c", "/notes/c.md")
        state.set_source_sections("src-c", ["3.2"], [3])

        cfg = ProjectConfig(chapters={3: "Methodology"})
        state.sync_section_types(cfg)
        r2 = state.sync_section_types(cfg)
        assert r2["updated"] == 0  # nothing left to update


# ── Repository queries with section_type ──────────────────────────────────


class TestRepositoryQueriesWithSectionType:
    def _seed(self, state):
        """Seed DB with sources assigned to typed sections."""
        state.register_sources(["method-src", "review-src"])
        state.mark_completed("method-src", "/notes/m.md", quality_score=4)
        state.mark_completed("review-src", "/notes/r.md", quality_score=3)
        state.set_source_sections("method-src", ["3.1"], [3])
        state.set_source_sections("review-src", ["2.1"], [2])
        state.save_fragments("method-src", [
            {"text": "Method fragment", "type": "methodology", "section": "3.1", "relevance": 5},
        ])
        state.save_fragments("review-src", [
            {"text": "Review fragment", "type": "key_idea", "section": "2.1", "relevance": 4},
        ])
        cfg = ProjectConfig(
            section_type_map={"3": "methodology", "2": "literature_review"},
        )
        state.sync_section_types(cfg)

    def test_get_by_section_type(self, state):
        self._seed(state)
        sources = state.get_by_section("", section_type="methodology")
        ids = [s["id"] for s in sources]
        assert "method-src" in ids
        assert "review-src" not in ids

    def test_get_by_section_numeric_still_works(self, state):
        self._seed(state)
        sources = state.get_by_section("3.1")
        ids = [s["id"] for s in sources]
        assert "method-src" in ids

    def test_get_fragments_by_section_type(self, state):
        self._seed(state)
        frags = state.get_fragments(section_type="methodology")
        assert len(frags) == 1
        assert frags[0]["fragment_text"] == "Method fragment"

    def test_get_fragments_by_section_type_no_match(self, state):
        self._seed(state)
        frags = state.get_fragments(section_type="conclusion")
        assert frags == []

    def test_coverage_stats_include_section_types(self, state):
        self._seed(state)
        cov = state.get_coverage_stats()
        assert "section_types" in cov
        assert cov["section_types"].get("methodology", 0) >= 1
        assert cov["section_types"].get("literature_review", 0) >= 1

    def test_get_section_sources_by_type(self, state):
        self._seed(state)
        ids = state.get_section_sources("", section_type="methodology")
        assert "method-src" in ids

    def test_get_sections_for_type(self, state):
        self._seed(state)
        sections = state.get_sections_for_type("methodology")
        assert "3" in sections

    def test_get_sections_for_type_empty(self, state):
        self._seed(state)
        assert state.get_sections_for_type("appendix") == []

    def test_coverage_stats_include_type_lookup(self, state):
        self._seed(state)
        cov = state.get_coverage_stats()
        lookup = cov.get("section_type_lookup", {})
        assert lookup.get("3") == "methodology"
        assert lookup.get("2") == "literature_review"
