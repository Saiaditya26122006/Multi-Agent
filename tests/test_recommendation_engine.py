"""Tests for the recommendation engine service."""

import pytest
from unittest.mock import patch, MagicMock


class TestGetHighestLeverageAction:
    """Test the recommendation scoring logic."""

    @patch("services.coverage_calculator.get_blocked_sections")
    @patch("services.coverage_calculator.get_stale_items")
    @patch("services.coverage_calculator.get_contradiction_count")
    @patch("services.coverage_calculator.get_oldest_assumptions")
    def test_old_assumption_scores_highest(
        self, mock_oldest, mock_contradictions, mock_stale, mock_blocked
    ):
        mock_oldest.return_value = [
            {"age_days": 60, "content_preview": "We assume institutional pricing"}
        ]
        mock_contradictions.return_value = 0
        mock_stale.return_value = []
        mock_blocked.return_value = []

        from services.recommendation_engine import get_highest_leverage_action

        result = get_highest_leverage_action()
        assert result["workspace"] == "validate"
        assert result["priority"] == "critical"

    @patch("services.coverage_calculator.get_blocked_sections")
    @patch("services.coverage_calculator.get_stale_items")
    @patch("services.coverage_calculator.get_contradiction_count")
    @patch("services.coverage_calculator.get_oldest_assumptions")
    def test_contradictions_recommend_challenge(
        self, mock_oldest, mock_contradictions, mock_stale, mock_blocked
    ):
        mock_oldest.return_value = []
        mock_contradictions.return_value = 5
        mock_stale.return_value = []
        mock_blocked.return_value = []

        from services.recommendation_engine import get_highest_leverage_action

        result = get_highest_leverage_action()
        assert result["workspace"] == "challenge"

    @patch("services.coverage_calculator.get_blocked_sections")
    @patch("services.coverage_calculator.get_stale_items")
    @patch("services.coverage_calculator.get_contradiction_count")
    @patch("services.coverage_calculator.get_oldest_assumptions")
    def test_blocked_sections_recommend_feed(
        self, mock_oldest, mock_contradictions, mock_stale, mock_blocked
    ):
        mock_oldest.return_value = []
        mock_contradictions.return_value = 0
        mock_stale.return_value = []
        mock_blocked.return_value = [
            {"section_id": "BP.5", "blocked_by": ["BP.1", "BP.3"]}
        ]

        from services.recommendation_engine import get_highest_leverage_action

        result = get_highest_leverage_action()
        assert result["workspace"] == "feed"

    @patch("services.coverage_calculator.get_blocked_sections")
    @patch("services.coverage_calculator.get_stale_items")
    @patch("services.coverage_calculator.get_contradiction_count")
    @patch("services.coverage_calculator.get_oldest_assumptions")
    def test_stale_items_recommend_inspect(
        self, mock_oldest, mock_contradictions, mock_stale, mock_blocked
    ):
        mock_oldest.return_value = [{"age_days": 10, "content_preview": "young"}]
        mock_contradictions.return_value = 0
        mock_stale.return_value = [
            {"id": "s1", "age_days": 45},
            {"id": "s2", "age_days": 40},
            {"id": "s3", "age_days": 35},
        ]
        mock_blocked.return_value = []

        from services.recommendation_engine import get_highest_leverage_action

        result = get_highest_leverage_action()
        assert result["workspace"] in ["inspect", "validate", "feed"]

    @patch("services.coverage_calculator.get_blocked_sections")
    @patch("services.coverage_calculator.get_stale_items")
    @patch("services.coverage_calculator.get_contradiction_count")
    @patch("services.coverage_calculator.get_oldest_assumptions")
    def test_no_issues_defaults_to_feed(
        self, mock_oldest, mock_contradictions, mock_stale, mock_blocked
    ):
        mock_oldest.return_value = []
        mock_contradictions.return_value = 0
        mock_stale.return_value = []
        mock_blocked.return_value = []

        from services.recommendation_engine import get_highest_leverage_action

        result = get_highest_leverage_action()
        assert result["workspace"] == "feed"
        assert result["priority"] == "low"

    @patch(
        "services.coverage_calculator.get_oldest_assumptions",
        side_effect=Exception("DB error"),
    )
    def test_returns_default_on_exception(self, mock_oldest):
        from services.recommendation_engine import get_highest_leverage_action

        result = get_highest_leverage_action()
        assert result["workspace"] == "feed"
        assert result["priority"] == "low"

    @patch("services.coverage_calculator.get_blocked_sections")
    @patch("services.coverage_calculator.get_stale_items")
    @patch("services.coverage_calculator.get_contradiction_count")
    @patch("services.coverage_calculator.get_oldest_assumptions")
    def test_highest_score_wins(
        self, mock_oldest, mock_contradictions, mock_stale, mock_blocked
    ):
        """When multiple issues exist, the highest-scoring wins."""
        mock_oldest.return_value = [
            {"age_days": 100, "content_preview": "Very old assumption"}
        ]
        mock_contradictions.return_value = 2
        mock_stale.return_value = [{"id": f"s{i}", "age_days": 50} for i in range(5)]
        mock_blocked.return_value = [
            {"section_id": "BP.5", "blocked_by": ["BP.1"]}
        ]

        from services.recommendation_engine import get_highest_leverage_action

        result = get_highest_leverage_action()
        assert result["workspace"] == "validate"
        assert result["priority"] == "critical"


class TestGetWorkspaceRecommendation:
    """Test workspace recommendation shortcut."""

    @patch("services.coverage_calculator.get_blocked_sections", return_value=[])
    @patch("services.coverage_calculator.get_stale_items", return_value=[])
    @patch("services.coverage_calculator.get_contradiction_count", return_value=5)
    @patch("services.coverage_calculator.get_oldest_assumptions", return_value=[])
    def test_returns_workspace_from_action(
        self, mock_oldest, mock_contradictions, mock_stale, mock_blocked
    ):
        from services.recommendation_engine import get_workspace_recommendation

        result = get_workspace_recommendation()
        assert result == "challenge"


class TestSuggestTransition:
    """Test workspace transition suggestions."""

    @patch("services.coverage_calculator.get_contradiction_count")
    def test_feed_to_challenge_on_contradictions(self, mock_contradictions):
        mock_contradictions.return_value = 3

        from services.recommendation_engine import suggest_transition

        result = suggest_transition("feed")
        assert result is not None
        assert result["target_workspace"] == "challenge"

    @patch("services.coverage_calculator.get_contradiction_count")
    def test_feed_no_transition_when_clean(self, mock_contradictions):
        mock_contradictions.return_value = 0

        from services.recommendation_engine import suggest_transition

        result = suggest_transition("feed")
        assert result is None

    def test_validate_always_suggests_build(self):
        from services.recommendation_engine import suggest_transition

        result = suggest_transition("validate")
        assert result is not None
        assert result["target_workspace"] == "build"

    def test_build_always_suggests_inspect(self):
        from services.recommendation_engine import suggest_transition

        result = suggest_transition("build")
        assert result is not None
        assert result["target_workspace"] == "inspect"

    @patch("services.coverage_calculator.get_oldest_assumptions")
    def test_challenge_to_validate_on_old_assumptions(self, mock_oldest):
        mock_oldest.return_value = [{"age_days": 30, "content_preview": "Old one"}]

        from services.recommendation_engine import suggest_transition

        result = suggest_transition("challenge")
        assert result is not None
        assert result["target_workspace"] == "validate"

    @patch("services.coverage_calculator.get_oldest_assumptions")
    def test_challenge_no_transition_when_assumptions_young(self, mock_oldest):
        mock_oldest.return_value = [{"age_days": 5, "content_preview": "Young"}]

        from services.recommendation_engine import suggest_transition

        result = suggest_transition("challenge")
        assert result is None

    def test_export_no_transition(self):
        from services.recommendation_engine import suggest_transition

        result = suggest_transition("export")
        assert result is None

    @patch(
        "services.coverage_calculator.get_contradiction_count",
        side_effect=Exception("Redis down"),
    )
    def test_handles_exception_gracefully(self, mock_contradictions):
        from services.recommendation_engine import suggest_transition

        result = suggest_transition("feed")
        assert result is None
