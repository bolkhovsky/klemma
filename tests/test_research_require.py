"""Tests for klemma research --require feature (issue #146).

TDD: tests written before implementation.
"""

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fragment(citekey, fragment_id, section="1.1", text=None):
    """Create a minimal fragment dict matching get_fragments() return shape."""
    return {
        "id": fragment_id,
        "source_id": citekey,
        "citekey": citekey,
        "fragment_text": text or f"Fragment from {citekey}: some text here.",
        "fragment_type": "background",
        "section": section,
        "chapter": 1,
        "relevance_score": 3,
        "usage_hint": "",
    }


def _make_state(tmp_path):
    """Create a real StateManager with test data."""
    from klemma.state import StateManager

    sm = StateManager(tmp_path / "test.db")
    sm.register_sources(["paper1", "paper2", "required_paper"])
    sm.mark_completed("paper1", note_path="@paper1.md")
    sm.mark_completed("paper2", note_path="@paper2.md")
    sm.mark_completed("required_paper", note_path="@required_paper.md")
    sm.set_source_sections("paper1", ["1.1"], [1])
    sm.set_source_sections("paper2", ["1.1"], [1])
    sm.set_source_sections("required_paper", ["1.1"], [1])

    sm.save_fragments(
        "paper1",
        [{"text": "Regular RAG fragment from paper1", "type": "background", "section": "1.1"}],
    )
    sm.save_fragments(
        "paper2",
        [{"text": "Regular RAG fragment from paper2", "type": "method", "section": "1.1"}],
    )
    sm.save_fragments(
        "required_paper",
        [{"text": "Important fragment that must be included", "type": "result", "section": "1.1"}],
    )
    return sm


# ---------------------------------------------------------------------------
# Unit tests for _get_required_fragments helper
# ---------------------------------------------------------------------------


class TestGetRequiredFragments:
    """Tests for the _get_required_fragments helper function."""

    def test_fetches_fragments_for_each_required_citekey(self):
        """get_fragments is called with source_id for each required citekey."""
        from klemma.skills.researcher import _get_required_fragments

        state = MagicMock()
        frag = _make_fragment("smith2020", "frag1")
        state.get_fragments.return_value = [frag]

        result_frags, missing = _get_required_fragments(["smith2020"], state, "1.1", 1)

        state.get_fragments.assert_called_once_with(source_id="smith2020", section="1.1", limit=10)
        assert len(result_frags) == 1
        assert result_frags[0]["citekey"] == "smith2020"
        assert missing == []

    def test_multiple_citekeys_each_fetched(self):
        """Each required citekey results in a separate get_fragments call."""
        from klemma.skills.researcher import _get_required_fragments

        state = MagicMock()
        state.get_fragments.side_effect = [
            [_make_fragment("alpha2020", "frag_a")],
            [_make_fragment("beta2021", "frag_b")],
        ]

        result_frags, missing = _get_required_fragments(
            ["alpha2020", "beta2021"], state, "1.3", 1
        )

        assert state.get_fragments.call_count == 2
        assert len(result_frags) == 2
        assert missing == []

    def test_warns_when_required_citekey_has_no_fragments(self):
        """Missing citekey returned in missing list when get_fragments returns empty."""
        from klemma.skills.researcher import _get_required_fragments

        state = MagicMock()
        state.get_fragments.return_value = []

        result_frags, missing = _get_required_fragments(["jones2019"], state, "1.1", 1)

        assert result_frags == []
        assert "jones2019" in missing

    def test_no_warning_when_fragments_found(self):
        """No missing citekeys when fragments exist."""
        from klemma.skills.researcher import _get_required_fragments

        state = MagicMock()
        state.get_fragments.return_value = [_make_fragment("brown2022", "frag_x")]

        _, missing = _get_required_fragments(["brown2022"], state, "2.1", 2)

        assert missing == []

    def test_empty_required_citekeys_returns_empty(self):
        """Empty list returns empty result without calling state."""
        from klemma.skills.researcher import _get_required_fragments

        state = MagicMock()

        result_frags, missing = _get_required_fragments([], state, "1.1", 1)

        state.get_fragments.assert_not_called()
        assert result_frags == []
        assert missing == []

    def test_mixed_found_and_missing(self):
        """Partial: some found, some missing."""
        from klemma.skills.researcher import _get_required_fragments

        state = MagicMock()
        state.get_fragments.side_effect = [
            [_make_fragment("found2020", "frag_f")],
            [],  # missing
        ]

        result_frags, missing = _get_required_fragments(
            ["found2020", "missing2021"], state, "1.1", 1
        )

        assert len(result_frags) == 1
        assert result_frags[0]["citekey"] == "found2020"
        assert missing == ["missing2021"]

    def test_none_chapter_passes_none(self):
        """When chapter is None, get_fragments is still called (no chapter filter)."""
        from klemma.skills.researcher import _get_required_fragments

        state = MagicMock()
        state.get_fragments.return_value = [_make_fragment("wang2023", "frag_w")]

        result_frags, missing = _get_required_fragments(["wang2023"], state, "methodology", None)

        state.get_fragments.assert_called_once_with(
            source_id="wang2023", section="methodology", limit=10
        )
        assert len(result_frags) == 1


# ---------------------------------------------------------------------------
# Integration tests: research_section() with required_citekeys
# ---------------------------------------------------------------------------


class TestResearchSectionRequire:
    """Tests for research_section() required_citekeys param."""

    def _mock_ai(self):
        ai = MagicMock()
        ai.call_json.return_value = {
            "section_status": "draft",
            "section_title": "Test Section",
            "current_word_count": 100,
            "target_word_count": 500,
            "readiness_pct": 20,
            "fragment_distribution": {},
            "argument_blocks": [],
            "citation_plan": [],
            "missing_coverage": [],
            "writing_suggestions": [],
        }
        ai.render_prompt.return_value = "test prompt"
        return ai

    def _mock_vault(self):
        vault = MagicMock()
        vault.read_note.return_value = None
        return vault

    def test_required_citekeys_get_fragments_called(self, tmp_path):
        """research_section calls get_fragments for required citekeys."""
        sm = _make_state(tmp_path)

        from klemma.config import KlemmaConfig
        from klemma.skills.researcher import research_section

        config = KlemmaConfig()

        with (
            patch("klemma.skills.researcher._load_chapter_draft", return_value=None),
            patch("klemma.skills.researcher._extract_section", return_value=None),
            patch("klemma.skills.researcher._load_section_sources", return_value=[]),
            patch("klemma.skills.researcher._fit_prompt_budget") as mock_budget,
            patch("klemma.skills.researcher._load_previous_research", return_value=None),
            patch("klemma.skills.researcher.resolve_prompt"),
            patch("klemma.skills.researcher._get_required_fragments") as mock_req,
        ):
            mock_budget.return_value = ("", [], [], None)
            mock_req.return_value = ([], [])

            research_section(
                "1.1",
                config,
                sm,
                self._mock_vault(),
                self._mock_ai(),
                required_citekeys=["required_paper"],
            )

            mock_req.assert_called_once()
            call_args = mock_req.call_args
            assert call_args[0][0] == ["required_paper"]  # first positional arg

    def test_required_fragments_prepended_to_section_fragments(self, tmp_path):
        """Required citekey fragments appear in available_fragments count."""
        sm = _make_state(tmp_path)

        from klemma.config import KlemmaConfig
        from klemma.skills.researcher import research_section

        config = KlemmaConfig()

        req_frag = _make_fragment("required_paper", "req_frag_id", text="Required fragment text")

        with (
            patch("klemma.skills.researcher._load_chapter_draft", return_value=None),
            patch("klemma.skills.researcher._extract_section", return_value=None),
            patch("klemma.skills.researcher._load_section_sources", return_value=[]),
            patch("klemma.skills.researcher._fit_prompt_budget") as mock_budget,
            patch("klemma.skills.researcher._load_previous_research", return_value=None),
            patch("klemma.skills.researcher.resolve_prompt"),
            patch("klemma.skills.researcher._get_required_fragments") as mock_req,
        ):
            # Regular RAG returns 0 fragments; required adds 1
            mock_budget.return_value = ("", [], [], None)
            mock_req.return_value = ([req_frag], [])

            result = research_section(
                "1.1",
                config,
                sm,
                self._mock_vault(),
                self._mock_ai(),
                required_citekeys=["required_paper"],
            )

            # available_fragments should include the required fragment
            assert result.available_fragments >= 1

    def test_required_fragments_dedup_against_rag(self, tmp_path):
        """Fragments already in RAG results not duplicated when also required.

        The fallback path (no embeddings) returns N fragments via state.get_fragments.
        Required also returns one fragment with the same id as a fallback result.
        After dedup: section_fragments count should be N, not N+1.
        """
        sm = _make_state(tmp_path)

        from klemma.config import KlemmaConfig
        from klemma.skills.researcher import research_section

        config = KlemmaConfig()

        # Get the actual fragment IDs from the real DB
        real_frags = sm.get_fragments(section="1.1", limit=50)
        assert len(real_frags) > 0, "Need at least 1 real fragment for this test"

        # Required returns one fragment already in the fallback results
        shared_frag = real_frags[0]
        base_count = len(real_frags)

        with (
            patch("klemma.skills.researcher._load_chapter_draft", return_value=None),
            patch("klemma.skills.researcher._extract_section", return_value=None),
            patch("klemma.skills.researcher._load_section_sources", return_value=[]),
            patch("klemma.skills.researcher._fit_prompt_budget") as mock_budget,
            patch("klemma.skills.researcher._load_previous_research", return_value=None),
            patch("klemma.skills.researcher.resolve_prompt"),
            patch("klemma.skills.researcher._get_required_fragments") as mock_req,
        ):
            mock_budget.return_value = ("", [], [], None)
            # Required returns the same fragment that's already in DB results
            mock_req.return_value = ([shared_frag], [])

            result = research_section(
                "1.1",
                config,
                sm,
                self._mock_vault(),
                self._mock_ai(),
                required_citekeys=[shared_frag["citekey"]],
            )

            # Dedup: shared_frag already in fallback results → count unchanged
            assert result.available_fragments == base_count, (
                f"Expected {base_count} fragments after dedup "
                f"(shared frag already present), got {result.available_fragments}"
            )

    def test_required_missing_warns_in_log_and_result(self, tmp_path):
        """Warning is logged and surfaced in ResearchResult.required_missing."""
        sm = _make_state(tmp_path)

        from klemma.config import KlemmaConfig
        from klemma.skills.researcher import research_section

        config = KlemmaConfig()

        with (
            patch("klemma.skills.researcher._load_chapter_draft", return_value=None),
            patch("klemma.skills.researcher._extract_section", return_value=None),
            patch("klemma.skills.researcher._load_section_sources", return_value=[]),
            patch("klemma.skills.researcher._fit_prompt_budget") as mock_budget,
            patch("klemma.skills.researcher._load_previous_research", return_value=None),
            patch("klemma.skills.researcher.resolve_prompt"),
            patch("klemma.skills.researcher._get_required_fragments") as mock_req,
            patch("klemma.skills.researcher.logger") as mock_logger,
        ):
            mock_budget.return_value = ("", [], [], None)
            # Simulate: "ghost_paper" has no fragments in this section
            mock_req.return_value = ([], ["ghost_paper"])

            result = research_section(
                "1.1",
                config,
                sm,
                self._mock_vault(),
                self._mock_ai(),
                required_citekeys=["ghost_paper"],
            )

            # Logger should warn about missing citekey
            assert mock_logger.warning.called
            warn_args = str(mock_logger.warning.call_args)
            assert "ghost_paper" in warn_args

            # Result should contain missing citekeys for CLI to display
            assert "ghost_paper" in result.required_missing

    def test_no_required_citekeys_unchanged_behavior(self, tmp_path):
        """research_section without required_citekeys works identically to before."""
        sm = _make_state(tmp_path)

        from klemma.config import KlemmaConfig
        from klemma.skills.researcher import research_section

        config = KlemmaConfig()

        with (
            patch("klemma.skills.researcher._load_chapter_draft", return_value=None),
            patch("klemma.skills.researcher._extract_section", return_value=None),
            patch("klemma.skills.researcher._load_section_sources", return_value=[]),
            patch("klemma.skills.researcher._fit_prompt_budget") as mock_budget,
            patch("klemma.skills.researcher._load_previous_research", return_value=None),
            patch("klemma.skills.researcher.resolve_prompt"),
            patch("klemma.skills.researcher._get_required_fragments") as mock_req,
        ):
            mock_budget.return_value = ("", [], [], None)

            result = research_section(
                "1.1",
                config,
                sm,
                self._mock_vault(),
                self._mock_ai(),
                # No required_citekeys
            )

            # _get_required_fragments should NOT be called
            mock_req.assert_not_called()
            # Result should still be valid
            assert result.section == "1.1"


# ---------------------------------------------------------------------------
# CLI comma-separated --require parsing (unit tests on the parsing logic)
# ---------------------------------------------------------------------------


def _parse_require(require_tuple):
    """Mirror the parsing logic in research() for isolated testing."""
    return [
        c.strip() for r in require_tuple for c in r.split(",") if c.strip()
    ] or None


def test_require_comma_separated_values():
    """--require key1,key2 splits into two citekeys."""
    assert _parse_require(("key1,key2",)) == ["key1", "key2"]


def test_require_comma_and_repeat():
    """--require key1,key2 --require key3 yields three citekeys."""
    assert set(_parse_require(("key1,key2", "key3"))) == {"key1", "key2", "key3"}


def test_require_single_no_comma():
    """--require key1 works without commas."""
    assert _parse_require(("key1",)) == ["key1"]


def test_require_strips_spaces():
    """--require 'key1, key2' strips whitespace."""
    assert _parse_require(("key1, key2",)) == ["key1", "key2"]


def test_require_empty_tuple_returns_none():
    """No --require flag → None (not empty list)."""
    assert _parse_require(()) is None
