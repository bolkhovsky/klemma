"""Tests for results-first writing order (Kallestinova 2011)."""

from klemma.section_types import get_writing_order


def test_writing_order_results_first():
    """Results/experiments sections come before introduction."""
    sections = {
        "1": "Introduction",
        "2": "Related Work",
        "3": "Methodology",
        "4": "Results",
        "5": "Discussion",
        "6": "Conclusion",
    }
    type_map = {
        "1": "introduction",
        "2": "literature_review",
        "3": "methodology",
        "4": "results",
        "5": "discussion",
        "6": "conclusion",
    }
    items = get_writing_order(sections, type_map)
    ids = [i.section_id for i in items]

    assert ids.index("4") < ids.index("1"), "Results before Introduction"
    assert ids.index("3") < ids.index("2"), "Methodology before Lit Review"
    assert ids.index("5") < ids.index("1"), "Discussion before Introduction"
    assert ids.index("1") < ids.index("6"), "Introduction before Conclusion"


def test_writing_order_infers_type_from_title():
    """When type_map is empty, infer from section titles."""
    sections = {
        "1": "Введение",
        "2": "Обзор литературы",
        "3": "Методология",
        "4": "Эксперименты",
        "5": "Заключение",
    }
    items = get_writing_order(sections, {})
    ids = [i.section_id for i in items]

    assert ids.index("4") < ids.index("1"), "Experiments before Introduction"
    assert ids.index("3") < ids.index("2"), "Methodology before Lit Review"


def test_writing_order_detects_drafts(tmp_path):
    """Sections with existing Draft_X.md files are marked has_draft=True."""
    drafts_dir = tmp_path / "notes" / "drafts"
    drafts_dir.mkdir(parents=True)
    (drafts_dir / "Draft_3.1.md").write_text("some draft content")

    sections = {"3.1": "Methodology", "3.2": "Data"}
    items = get_writing_order(sections, {}, drafts_dir)

    by_id = {i.section_id: i for i in items}
    assert by_id["3.1"].has_draft is True
    assert by_id["3.2"].has_draft is False


def test_writing_order_empty_sections():
    """Empty sections dict returns empty list."""
    assert get_writing_order({}, {}) == []


def test_writing_order_unknown_type_gets_default_priority():
    """Sections with no type match get priority 3 (middle)."""
    sections = {"1": "Some Custom Section"}
    items = get_writing_order(sections, {})
    assert len(items) == 1
    assert items[0].priority == 3
    assert items[0].section_type is None


def test_writing_order_priority_values():
    """Verify specific priority assignments."""
    sections = {
        "1": "Intro",
        "2": "Methods",
        "3": "Results",
        "4": "Conclusion",
    }
    type_map = {
        "1": "introduction",
        "2": "methodology",
        "3": "results",
        "4": "conclusion",
    }
    items = get_writing_order(sections, type_map)
    by_id = {i.section_id: i for i in items}

    assert by_id["3"].priority == 1  # results
    assert by_id["2"].priority == 2  # methodology
    assert by_id["1"].priority == 5  # introduction
    assert by_id["4"].priority == 6  # conclusion


def test_writing_order_sorts_by_section_id_within_priority():
    """Sections with same priority are sorted by section_id."""
    sections = {
        "4.2": "Experiment B",
        "4.1": "Experiment A",
        "5.1": "Result A",
    }
    type_map = {
        "4.1": "experiments",
        "4.2": "experiments",
        "5.1": "results",
    }
    items = get_writing_order(sections, type_map)
    ids = [i.section_id for i in items]
    assert ids == ["4.1", "4.2", "5.1"]


def test_writing_order_no_drafts_dir():
    """When drafts_dir is None, all has_draft are False."""
    sections = {"1": "Intro"}
    items = get_writing_order(sections, {}, None)
    assert items[0].has_draft is False
