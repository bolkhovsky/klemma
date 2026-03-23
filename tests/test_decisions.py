"""Tests for Guided Serendipity decisions repository and migration."""

import pytest

from klemma.state import StateManager


@pytest.fixture
def state(tmp_path):
    """Create a StateManager with a fresh DB."""
    db_path = tmp_path / "test.db"
    return StateManager(db_path)


class TestDecisionsMigration:
    """Verify v14 migration creates the decisions table with note/feedback."""

    def test_decisions_table_exists(self, state):
        with state._conn() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "decisions" in tables

    def test_db_version_is_14(self, state):
        with state._conn() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            assert version == 14

    def test_decisions_columns(self, state):
        with state._conn() as conn:
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(decisions)")
            }
            expected = {
                "id",
                "created_at",
                "decided_at",
                "trigger_type",
                "trigger_source",
                "context_json",
                "options_json",
                "chosen_option",
                "rationale",
                "sections",
                "influenced_by",
                "note",
                "feedback",
            }
            assert expected == cols

    def test_indexes_created(self, state):
        with state._conn() as conn:
            indexes = {
                row[1]
                for row in conn.execute(
                    "SELECT * FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='decisions'"
                )
                if row[1]
            }
            assert "idx_decisions_pending" in indexes
            assert "idx_decisions_source" in indexes


class TestDecisionsRepository:
    """Test CRUD operations on DecisionsRepository."""

    def test_save_and_get_decision(self, state):
        decision_id = state.decisions.save_decision(
            trigger_type="briefing",
            trigger_source="goessling2016",
            context={"key_claims": ["IIEE splits error into 3 components"]},
            options=[
                {"key": "A", "title": "Use as one metric"},
                {"key": "B", "title": "Make displacement central"},
                {"key": "C", "title": "Propose Arctic modification"},
            ],
            sections=["3.2"],
        )
        assert decision_id > 0

        d = state.decisions.get_decision(decision_id)
        assert d is not None
        assert d["trigger_type"] == "briefing"
        assert d["trigger_source"] == "goessling2016"
        assert d["chosen_option"] is None
        assert len(d["options_json"]) == 3
        assert d["sections"] == ["3.2"]

    def test_decide(self, state):
        decision_id = state.decisions.save_decision(
            trigger_type="briefing",
            trigger_source="test2024",
            context={},
            options=[{"key": "A", "title": "Option A"}, {"key": "B", "title": "Option B"}],
        )

        result = state.decisions.decide(decision_id, "B", "Aligns with my thesis")
        assert result is True

        d = state.decisions.get_decision(decision_id)
        assert d["chosen_option"] == "B"
        assert d["rationale"] == "Aligns with my thesis"
        assert d["decided_at"] is not None

    def test_decide_already_decided(self, state):
        decision_id = state.decisions.save_decision(
            trigger_type="briefing",
            trigger_source="test2024",
            context={},
            options=[{"key": "A", "title": "A"}],
        )
        state.decisions.decide(decision_id, "A")

        # Second decide should fail (already decided)
        result = state.decisions.decide(decision_id, "B")
        assert result is False

        # Original choice preserved
        d = state.decisions.get_decision(decision_id)
        assert d["chosen_option"] == "A"

    def test_skip_decision(self, state):
        decision_id = state.decisions.save_decision(
            trigger_type="insight",
            context={"type": "blind_spot"},
            options=[{"key": "A", "title": "Explore"}, {"key": "B", "title": "Ignore"}],
        )

        result = state.decisions.skip_decision(decision_id)
        assert result is True

        d = state.decisions.get_decision(decision_id)
        assert d["chosen_option"] == "__skipped__"

    def test_get_pending_decisions(self, state):
        # Create 3 decisions: 1 decided, 1 pending, 1 skipped
        id1 = state.decisions.save_decision(
            trigger_type="briefing", context={}, options=[]
        )
        id2 = state.decisions.save_decision(
            trigger_type="insight", context={}, options=[]
        )
        id3 = state.decisions.save_decision(
            trigger_type="briefing", context={}, options=[]
        )
        state.decisions.decide(id1, "A")
        state.decisions.skip_decision(id3)

        pending = state.decisions.get_pending_decisions()
        assert len(pending) == 1
        assert pending[0]["id"] == id2

    def test_get_pending_by_type(self, state):
        state.decisions.save_decision(
            trigger_type="briefing", context={}, options=[]
        )
        state.decisions.save_decision(
            trigger_type="insight", context={}, options=[]
        )

        briefing_pending = state.decisions.get_pending_decisions(
            trigger_type="briefing"
        )
        assert len(briefing_pending) == 1

    def test_get_decisions_filters(self, state):
        state.decisions.save_decision(
            trigger_type="briefing",
            context={},
            options=[],
            sections=["3.2"],
        )
        state.decisions.save_decision(
            trigger_type="insight",
            context={},
            options=[],
            sections=["4.1"],
        )

        by_section = state.decisions.get_decisions(section="3.2")
        assert len(by_section) == 1

        by_type = state.decisions.get_decisions(trigger_type="insight")
        assert len(by_type) == 1

    def test_get_trail(self, state):
        id1 = state.decisions.save_decision(
            trigger_type="briefing",
            trigger_source="paper1",
            context={},
            options=[{"key": "A", "title": "Direction A"}],
        )
        id2 = state.decisions.save_decision(
            trigger_type="briefing",
            trigger_source="paper2",
            context={},
            options=[{"key": "B", "title": "Direction B"}],
            influenced_by=[id1],
        )
        id3 = state.decisions.save_decision(
            trigger_type="insight",
            context={},
            options=[{"key": "A", "title": "Explore"}],
        )

        state.decisions.decide(id1, "A")
        state.decisions.decide(id2, "B")
        state.decisions.skip_decision(id3)

        trail = state.decisions.get_trail()
        assert len(trail) == 2  # skipped excluded
        assert trail[0]["trigger_source"] == "paper1"
        assert trail[1]["influenced_by"] == [id1]

    def test_count_decisions(self, state):
        id1 = state.decisions.save_decision(
            trigger_type="briefing", context={}, options=[]
        )
        _id2 = state.decisions.save_decision(  # noqa: F841
            trigger_type="insight", context={}, options=[]
        )
        id3 = state.decisions.save_decision(
            trigger_type="briefing", context={}, options=[]
        )
        state.decisions.decide(id1, "A")
        state.decisions.skip_decision(id3)

        counts = state.decisions.count_decisions()
        assert counts["total"] == 3
        assert counts["decided"] == 1
        assert counts["pending"] == 1
        assert counts["skipped"] == 1

    def test_get_decisions_for_context(self, state):
        id1 = state.decisions.save_decision(
            trigger_type="briefing",
            trigger_source="paper1",
            context={},
            options=[],
            sections=["3.2", "3.3"],
        )
        state.decisions.decide(id1, "A", "Focus on IIEE")

        # Should find by section
        ctx_decisions = state.decisions.get_decisions_for_context(
            sections=["3.2"]
        )
        assert len(ctx_decisions) == 1
        assert ctx_decisions[0]["chosen_option"] == "A"

        # Should not find for unrelated section
        ctx_decisions = state.decisions.get_decisions_for_context(
            sections=["5.1"]
        )
        assert len(ctx_decisions) == 0

    def test_influenced_by_stored(self, state):
        id1 = state.decisions.save_decision(
            trigger_type="briefing", context={}, options=[]
        )
        id2 = state.decisions.save_decision(
            trigger_type="briefing",
            context={},
            options=[],
            influenced_by=[id1],
        )

        d = state.decisions.get_decision(id2)
        assert d["influenced_by"] == [id1]

    def test_nonexistent_decision(self, state):
        assert state.decisions.get_decision(999) is None
        assert state.decisions.decide(999, "A") is False

    def test_add_note(self, state):
        decision_id = state.decisions.save_decision(
            trigger_type="insight",
            context={"type": "blind_spot"},
            options=[{"key": "A", "title": "Act"}],
        )
        result = state.decisions.add_note(decision_id, "сопоставить IIEE с SPS")
        assert result is True

        d = state.decisions.get_decision(decision_id)
        assert d["note"] == "сопоставить IIEE с SPS"

    def test_add_note_overwrites(self, state):
        decision_id = state.decisions.save_decision(
            trigger_type="insight", context={}, options=[]
        )
        state.decisions.add_note(decision_id, "first note")
        state.decisions.add_note(decision_id, "updated note")

        d = state.decisions.get_decision(decision_id)
        assert d["note"] == "updated note"

    def test_add_note_nonexistent(self, state):
        result = state.decisions.add_note(999, "note")
        assert result is False

    def test_set_feedback_like(self, state):
        decision_id = state.decisions.save_decision(
            trigger_type="insight",
            context={"type": "hidden_cluster"},
            options=[],
        )
        result = state.decisions.set_feedback(decision_id, "like")
        assert result is True

        d = state.decisions.get_decision(decision_id)
        assert d["feedback"] == "like"

    def test_set_feedback_dislike(self, state):
        decision_id = state.decisions.save_decision(
            trigger_type="insight", context={}, options=[]
        )
        result = state.decisions.set_feedback(decision_id, "dislike")
        assert result is True

        d = state.decisions.get_decision(decision_id)
        assert d["feedback"] == "dislike"

    def test_set_feedback_invalid(self, state):
        decision_id = state.decisions.save_decision(
            trigger_type="insight", context={}, options=[]
        )
        with pytest.raises(ValueError, match="feedback must be"):
            state.decisions.set_feedback(decision_id, "neutral")

    def test_set_feedback_nonexistent(self, state):
        result = state.decisions.set_feedback(999, "like")
        assert result is False

    def test_get_feedback_summary_empty(self, state):
        summary = state.decisions.get_feedback_summary()
        assert summary["total_liked"] == 0
        assert summary["total_disliked"] == 0
        assert summary["recent_notes"] == []

    def test_get_feedback_summary(self, state):
        # Create insight decisions with different types and feedback
        id1 = state.decisions.save_decision(
            trigger_type="insight",
            context={"type": "blind_spot"},
            options=[],
        )
        id2 = state.decisions.save_decision(
            trigger_type="insight",
            context={"type": "blind_spot"},
            options=[],
        )
        id3 = state.decisions.save_decision(
            trigger_type="insight",
            context={"type": "hidden_cluster"},
            options=[],
        )
        id4 = state.decisions.save_decision(
            trigger_type="insight",
            context={"type": "hidden_cluster"},
            options=[],
        )

        state.decisions.set_feedback(id1, "like")
        state.decisions.set_feedback(id2, "like")
        state.decisions.set_feedback(id3, "dislike")
        state.decisions.set_feedback(id4, "like")

        # Add a note
        state.decisions.add_note(id1, "interesting IIEE pattern")

        summary = state.decisions.get_feedback_summary()
        assert summary["total_liked"] == 3
        assert summary["total_disliked"] == 1
        assert summary["liked_types"]["blind_spot"] == 2
        assert summary["liked_types"]["hidden_cluster"] == 1
        assert summary["disliked_types"]["hidden_cluster"] == 1
        assert "interesting IIEE pattern" in summary["recent_notes"]

    def test_feedback_summary_ignores_non_insight(self, state):
        """Feedback summary only counts insight decisions, not briefings."""
        id1 = state.decisions.save_decision(
            trigger_type="briefing",
            context={"type": "briefing_ctx"},
            options=[],
        )
        state.decisions.set_feedback(id1, "like")

        summary = state.decisions.get_feedback_summary()
        assert summary["total_liked"] == 0
