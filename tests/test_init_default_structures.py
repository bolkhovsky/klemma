"""Tests for type-aware default structures in klemma init."""

import warnings

import pytest

from klemma.config import parse_klemma_md
from klemma.setup import (
    InitValues,
    _build_klemma_md,
    _get_default_structure,
    _validate_outline_for_type,
)


def _parse_built_md(values: InitValues) -> tuple[dict, str]:
    """Build KLEMMA.md from values, write to tmp, parse back."""
    import tempfile
    from pathlib import Path

    content = _build_klemma_md(values)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp = Path(f.name)
    try:
        return parse_klemma_md(tmp)
    finally:
        tmp.unlink()


class TestDefaultStructuresRegistry:
    def test_dissertation_en(self):
        s = _get_default_structure("dissertation", "en")
        assert s["chapters"] == {1: "Literature review", 2: "Methodology", 3: "Results and discussion"}
        assert "scientific_results" in s
        assert s["auto_register"] == "mapped"
        assert s["min_sources_per_section"] == 3

    def test_dissertation_ru(self):
        s = _get_default_structure("dissertation", "ru")
        assert s["chapters"][1] == "Обзор литературы"
        assert "scientific_results" in s

    def test_paper_en(self):
        s = _get_default_structure("paper", "en")
        assert list(s["chapters"].values()) == ["Introduction", "Methods", "Results", "Discussion"]
        assert "scientific_results" not in s
        assert s["auto_register"] == "none"
        assert s["min_sources_per_section"] == 2

    def test_paper_ru(self):
        s = _get_default_structure("paper", "ru")
        assert list(s["chapters"].values()) == ["Введение", "Методы", "Результаты", "Обсуждение"]
        assert "scientific_results" not in s

    def test_thesis_en(self):
        s = _get_default_structure("thesis", "en")
        assert list(s["chapters"].values()) == ["Problem statement", "Results"]
        assert "scientific_results" not in s
        assert s["min_sources_per_section"] == 1

    def test_thesis_ru(self):
        s = _get_default_structure("thesis", "ru")
        assert list(s["chapters"].values()) == ["Постановка задачи", "Результаты"]

    def test_fallback_to_english(self):
        s = _get_default_structure("paper", "de")
        assert s == _get_default_structure("paper", "en")

    def test_unknown_type_falls_back_to_dissertation_en(self):
        s = _get_default_structure("unknown_type", "en")
        assert s == _get_default_structure("dissertation", "en")


class TestBuildKlemmaMdDissertationEn:
    """Backward compat: dissertation/en should produce same structure as before."""

    def test_chapters(self):
        fm, _ = _parse_built_md(InitValues(project_type="dissertation", language="en", title="Test"))
        assert fm["chapters"] == {1: "Literature review", 2: "Methodology", 3: "Results and discussion"}

    def test_scientific_results(self):
        fm, _ = _parse_built_md(InitValues(project_type="dissertation", language="en", title="Test"))
        assert "nr1" in fm["scientific_results"]
        assert "nr2" in fm["scientific_results"]

    def test_auto_register_mapped(self):
        fm, _ = _parse_built_md(InitValues(project_type="dissertation", language="en", title="Test"))
        assert fm["auto_register"] == "mapped"

    def test_min_sources(self):
        fm, _ = _parse_built_md(InitValues(project_type="dissertation", language="en", title="Test"))
        assert fm["min_sources_per_section"] == 3

    def test_body_has_scientific_results_heading(self):
        _, body = _parse_built_md(InitValues(project_type="dissertation", language="en", title="Test"))
        assert "## Scientific Results" in body

    def test_body_has_structure_heading(self):
        _, body = _parse_built_md(InitValues(project_type="dissertation", language="en", title="Test"))
        assert "## Structure" in body


class TestBuildKlemmaMdDissertationRu:
    def test_russian_chapters(self):
        fm, _ = _parse_built_md(InitValues(project_type="dissertation", language="ru", title="Тест"))
        assert fm["chapters"][1] == "Обзор литературы"
        assert fm["chapters"][2] == "Методология"
        assert fm["chapters"][3] == "Результаты и обсуждение"

    def test_russian_scientific_results(self):
        fm, _ = _parse_built_md(InitValues(project_type="dissertation", language="ru", title="Тест"))
        assert fm["scientific_results"]["nr1"] == "Первый научный результат"


class TestBuildKlemmaMdPaperEn:
    def test_imrad_sections(self):
        fm, _ = _parse_built_md(InitValues(project_type="paper", language="en", title="Paper"))
        assert list(fm["chapters"].values()) == ["Introduction", "Methods", "Results", "Discussion"]

    def test_no_scientific_results(self):
        fm, body = _parse_built_md(InitValues(project_type="paper", language="en", title="Paper"))
        assert "scientific_results" not in fm
        assert "## Scientific Results" not in body

    def test_auto_register_none(self):
        fm, _ = _parse_built_md(InitValues(project_type="paper", language="en", title="Paper"))
        assert fm["auto_register"] == "none"

    def test_min_sources_2(self):
        fm, _ = _parse_built_md(InitValues(project_type="paper", language="en", title="Paper"))
        assert fm["min_sources_per_section"] == 2

    def test_body_has_sections_heading(self):
        _, body = _parse_built_md(InitValues(project_type="paper", language="en", title="Paper"))
        assert "## Sections" in body
        assert "## Structure" not in body

    def test_section_type_map_inferred(self):
        fm, _ = _parse_built_md(InitValues(project_type="paper", language="en", title="Paper"))
        stm = fm.get("section_type_map", {})
        assert stm.get("1") == "introduction"
        assert stm.get("2") == "methodology"
        assert stm.get("3") == "results"
        assert stm.get("4") == "discussion"


class TestBuildKlemmaMdPaperRu:
    def test_russian_imrad(self):
        fm, _ = _parse_built_md(InitValues(project_type="paper", language="ru", title="Статья"))
        assert list(fm["chapters"].values()) == ["Введение", "Методы", "Результаты", "Обсуждение"]

    def test_section_type_map_inferred_ru(self):
        fm, _ = _parse_built_md(InitValues(project_type="paper", language="ru", title="Статья"))
        stm = fm.get("section_type_map", {})
        assert stm.get("1") == "introduction"
        assert stm.get("3") == "results"


class TestBuildKlemmaMdThesisEn:
    def test_two_sections(self):
        fm, _ = _parse_built_md(InitValues(project_type="thesis", language="en", title="Thesis"))
        assert list(fm["chapters"].values()) == ["Problem statement", "Results"]

    def test_no_scientific_results(self):
        fm, body = _parse_built_md(InitValues(project_type="thesis", language="en", title="Thesis"))
        assert "scientific_results" not in fm
        assert "## Scientific Results" not in body

    def test_min_sources_1(self):
        fm, _ = _parse_built_md(InitValues(project_type="thesis", language="en", title="Thesis"))
        assert fm["min_sources_per_section"] == 1

    def test_auto_register_mapped(self):
        fm, _ = _parse_built_md(InitValues(project_type="thesis", language="en", title="Thesis"))
        assert fm["auto_register"] == "mapped"

    def test_section_type_map_inferred(self):
        fm, _ = _parse_built_md(InitValues(project_type="thesis", language="en", title="Thesis"))
        stm = fm.get("section_type_map", {})
        assert stm.get("2") == "results"


class TestBuildKlemmaMdThesisRu:
    def test_russian_sections(self):
        fm, _ = _parse_built_md(InitValues(project_type="thesis", language="ru", title="Тезисы"))
        assert list(fm["chapters"].values()) == ["Постановка задачи", "Результаты"]


class TestSectionTypeMapInference:
    """Verify section_type_map is auto-inferred for all default structures."""

    @pytest.mark.parametrize("ptype,lang", [
        ("dissertation", "en"),
        ("dissertation", "ru"),
        ("paper", "en"),
        ("paper", "ru"),
        ("thesis", "en"),
        ("thesis", "ru"),
    ])
    def test_section_type_map_present(self, ptype, lang):
        fm, _ = _parse_built_md(InitValues(project_type=ptype, language=lang, title="Test"))
        assert "section_type_map" in fm, f"No section_type_map for {ptype}/{lang}"
        assert len(fm["section_type_map"]) > 0


class TestValidateOutlineForType:
    def _make_plan(self, n_chapters):
        """Create a mock plan_data with n chapters."""
        class FakeChapter:
            def __init__(self, num, title):
                self.number = num
                self.title = f"Chapter {num}"
                self.sections = []

        class FakePlan:
            def __init__(self, chapters):
                self.chapters = chapters
                self.results = []
                self.title = "Test"

        return FakePlan([FakeChapter(i + 1, f"Ch {i + 1}") for i in range(n_chapters)])

    def test_paper_many_chapters_warns(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _validate_outline_for_type("paper", self._make_plan(8))
            assert len(w) == 1
            assert "8 sections" in str(w[0].message)

    def test_paper_normal_no_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _validate_outline_for_type("paper", self._make_plan(4))
            assert len(w) == 0

    def test_thesis_many_chapters_warns(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _validate_outline_for_type("thesis", self._make_plan(5))
            assert len(w) == 1
            assert "5 sections" in str(w[0].message)

    def test_dissertation_too_few_warns(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _validate_outline_for_type("dissertation", self._make_plan(1))
            assert len(w) == 1
            assert "1 chapter" in str(w[0].message)

    def test_dissertation_normal_no_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _validate_outline_for_type("dissertation", self._make_plan(3))
            assert len(w) == 0

    def test_no_plan_data_no_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _validate_outline_for_type("paper", None)
            assert len(w) == 0


class TestPlanDataNonDissertation:
    """Test that plan_data works for paper/thesis types."""

    def _make_plan(self, chapters_list):
        class FakeChapter:
            def __init__(self, num, title):
                self.number = num
                self.title = title
                self.sections = []

        class FakePlan:
            def __init__(self, chapters):
                self.chapters = chapters
                self.results = []
                self.title = "Plan Title"

        return FakePlan([FakeChapter(n, t) for n, t in chapters_list])

    def test_paper_with_plan_data(self):
        plan = self._make_plan([
            (1, "Introduction"),
            (2, "Data and Methods"),
            (3, "Experiments"),
            (4, "Results"),
            (5, "Conclusion"),
        ])
        fm, _ = _parse_built_md(InitValues(
            project_type="paper", language="en", title="My Paper", plan_data=plan,
        ))
        assert fm["chapters"] == {
            1: "Introduction", 2: "Data and Methods",
            3: "Experiments", 4: "Results", 5: "Conclusion",
        }
        assert fm["auto_register"] == "none"
        assert fm["min_sources_per_section"] == 2
        assert "scientific_results" not in fm

    def test_thesis_with_plan_data(self):
        plan = self._make_plan([(1, "Problem"), (2, "Results")])
        fm, _ = _parse_built_md(InitValues(
            project_type="thesis", language="ru", title="Тезисы", plan_data=plan,
        ))
        assert fm["chapters"] == {1: "Problem", 2: "Results"}
        assert fm["min_sources_per_section"] == 1
        assert fm["auto_register"] == "mapped"

    def test_plan_data_section_type_map(self):
        plan = self._make_plan([(1, "Introduction"), (2, "Methods"), (3, "Results")])
        fm, _ = _parse_built_md(InitValues(
            project_type="paper", language="en", title="P", plan_data=plan,
        ))
        stm = fm.get("section_type_map", {})
        assert stm.get("1") == "introduction"
        assert stm.get("2") == "methodology"
        assert stm.get("3") == "results"


class TestInitProjectIntegration:
    """Integration tests via init_project (full flow)."""

    def test_paper_init(self, tmp_path):
        from klemma.setup import init_project

        values = InitValues(project_type="paper", title="Test Paper", language="en")
        result = init_project(tmp_path, project_type="paper", values=values)
        assert "KLEMMA.md" in result["created"]

        fm, body = parse_klemma_md(tmp_path / "KLEMMA.md")
        assert fm["type"] == "paper"
        assert list(fm["chapters"].values()) == ["Introduction", "Methods", "Results", "Discussion"]
        assert "scientific_results" not in fm
        assert "## Sections" in body

    def test_thesis_ru_init(self, tmp_path):
        from klemma.setup import init_project

        values = InitValues(project_type="thesis", title="Тезисы", language="ru")
        init_project(tmp_path, project_type="thesis", values=values)

        fm, body = parse_klemma_md(tmp_path / "KLEMMA.md")
        assert fm["type"] == "thesis"
        assert list(fm["chapters"].values()) == ["Постановка задачи", "Результаты"]
        assert "scientific_results" not in fm
        assert fm["min_sources_per_section"] == 1

    def test_dissertation_en_backward_compat(self, tmp_path):
        from klemma.setup import init_project

        values = InitValues(project_type="dissertation", title="Diss", language="en")
        init_project(tmp_path, project_type="dissertation", values=values)

        fm, body = parse_klemma_md(tmp_path / "KLEMMA.md")
        assert fm["type"] == "dissertation"
        assert fm["chapters"] == {1: "Literature review", 2: "Methodology", 3: "Results and discussion"}
        assert "scientific_results" in fm
        assert fm["auto_register"] == "mapped"
        assert "## Structure" in body
