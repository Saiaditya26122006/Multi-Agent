"""Tests for the menu generator."""

import pytest
from unittest.mock import patch, MagicMock

from web.menu_generator import (
    generate_main_menu,
    generate_sub_menu,
    generate_dashboard_stats,
    generate_recommendation,
    format_menu_as_text,
    format_sub_menu_as_text,
)
from web.workspace_router import Workspace


class TestDashboardStats:
    """Test dashboard statistics generation."""

    @patch("services.coverage_calculator.get_dashboard_stats")
    def test_returns_stats_on_success(self, mock_stats):
        mock_stats.return_value = {
            "coverage_pct": 47.0,
            "confidence_pct": 23.0,
            "contradiction_count": 9,
            "stale_count": 4,
            "oldest_assumption_age_days": 42,
        }
        result = generate_dashboard_stats()
        assert result["coverage_pct"] == 47.0
        assert result["contradiction_count"] == 9

    @patch("services.coverage_calculator.get_dashboard_stats", side_effect=Exception("fail"))
    def test_returns_defaults_on_error(self, mock_stats):
        result = generate_dashboard_stats()
        assert result["coverage_pct"] == 0.0
        assert result["contradiction_count"] == 0


class TestRecommendation:
    """Test recommendation generation."""

    @patch("services.recommendation_engine.get_highest_leverage_action")
    def test_returns_action(self, mock_action):
        mock_action.return_value = {
            "action_text": "Validate pricing assumption",
            "workspace": "validate",
            "priority": "critical",
            "reasoning": "42 days old",
        }
        result = generate_recommendation()
        assert result["workspace"] == "validate"
        assert "pricing" in result["action_text"]


class TestMainMenu:
    """Test main menu generation."""

    @patch("services.coverage_calculator.get_blocked_sections", return_value=[])
    @patch("services.recommendation_engine.get_highest_leverage_action")
    @patch("services.coverage_calculator.get_dashboard_stats")
    def test_menu_has_all_workspaces(self, mock_dash, mock_rec, mock_blocked):
        mock_dash.return_value = {
            "coverage_pct": 50.0,
            "confidence_pct": 30.0,
            "contradiction_count": 2,
            "stale_count": 1,
            "oldest_assumption_age_days": 10,
        }
        mock_rec.return_value = {
            "action_text": "Feed data",
            "workspace": "feed",
            "priority": "low",
            "reasoning": "",
        }

        menu = generate_main_menu()
        assert len(menu["items"]) == 7
        ids = [item["id"] for item in menu["items"]]
        assert "feed" in ids
        assert "build" in ids
        assert "inspect" in ids
        assert "challenge" in ids
        assert "validate" in ids
        assert "export" in ids
        assert "auto" in ids

    @patch("services.coverage_calculator.get_blocked_sections", return_value=[])
    @patch("services.recommendation_engine.get_highest_leverage_action")
    @patch("services.coverage_calculator.get_dashboard_stats")
    def test_menu_items_have_required_fields(self, mock_dash, mock_rec, mock_blocked):
        mock_dash.return_value = {
            "coverage_pct": 0,
            "confidence_pct": 0,
            "contradiction_count": 0,
            "stale_count": 0,
            "oldest_assumption_age_days": 0,
        }
        mock_rec.return_value = {
            "action_text": "",
            "workspace": "feed",
            "priority": "low",
            "reasoning": "",
        }

        menu = generate_main_menu()
        for item in menu["items"]:
            assert "id" in item
            assert "number" in item
            assert "label" in item
            assert "description" in item
            assert "status" in item

    @patch("services.coverage_calculator.get_blocked_sections", return_value=[])
    @patch("services.recommendation_engine.get_highest_leverage_action")
    @patch("services.coverage_calculator.get_dashboard_stats")
    def test_urgent_badge_on_old_assumptions(self, mock_dash, mock_rec, mock_blocked):
        mock_dash.return_value = {
            "coverage_pct": 50.0,
            "confidence_pct": 30.0,
            "contradiction_count": 0,
            "stale_count": 0,
            "oldest_assumption_age_days": 45,
        }
        mock_rec.return_value = {
            "action_text": "",
            "workspace": "validate",
            "priority": "critical",
            "reasoning": "",
        }

        menu = generate_main_menu()
        validate_item = next(i for i in menu["items"] if i["id"] == "validate")
        assert validate_item["status"] == "urgent"
        assert "45d" in validate_item["badge"]


class TestSubMenu:
    """Test sub-menu generation per workspace."""

    @patch("services.coverage_calculator.get_stale_items", return_value=[])
    def test_feed_sub_menu_has_options(self, mock_stale):
        sub = generate_sub_menu(Workspace.FEED)
        assert sub["workspace"] == "feed"
        assert len(sub["options"]) == 5
        keys = [o["key"] for o in sub["options"]]
        assert keys == ["A", "B", "C", "D", "E"]

    @patch("services.coverage_calculator.get_blocked_sections", return_value=[])
    def test_build_sub_menu_has_options(self, mock_blocked):
        sub = generate_sub_menu(Workspace.BUILD)
        assert sub["workspace"] == "build"
        assert len(sub["options"]) == 4

    def test_inspect_sub_menu_has_options(self):
        sub = generate_sub_menu(Workspace.INSPECT)
        assert sub["workspace"] == "inspect"
        assert len(sub["options"]) == 6

    @patch("services.coverage_calculator.get_oldest_assumptions", return_value=[])
    def test_challenge_sub_menu_has_options(self, mock_oldest):
        sub = generate_sub_menu(Workspace.CHALLENGE)
        assert sub["workspace"] == "challenge"
        assert len(sub["options"]) == 5

    @patch("services.coverage_calculator.get_oldest_assumptions", return_value=[])
    def test_validate_sub_menu_has_options(self, mock_oldest):
        sub = generate_sub_menu(Workspace.VALIDATE)
        assert sub["workspace"] == "validate"
        assert len(sub["options"]) == 4

    @patch("services.coverage_calculator.get_dashboard_stats", return_value={"coverage_pct": 50})
    def test_export_sub_menu_has_options(self, mock_dash):
        sub = generate_sub_menu(Workspace.EXPORT)
        assert sub["workspace"] == "export"
        assert len(sub["options"]) == 5


class TestFormatting:
    """Test text formatting of menus."""

    @patch("services.coverage_calculator.get_blocked_sections", return_value=[])
    @patch("services.recommendation_engine.get_highest_leverage_action")
    @patch("services.coverage_calculator.get_dashboard_stats")
    def test_format_menu_contains_workspaces(self, mock_dash, mock_rec, mock_blocked):
        mock_dash.return_value = {
            "coverage_pct": 47.0,
            "confidence_pct": 23.0,
            "contradiction_count": 9,
            "stale_count": 4,
            "oldest_assumption_age_days": 42,
        }
        mock_rec.return_value = {
            "action_text": "Validate pricing",
            "workspace": "validate",
            "priority": "critical",
            "reasoning": "",
        }

        menu = generate_main_menu()
        text = format_menu_as_text(menu)
        assert "Feed Data" in text
        assert "Build Plan" in text
        assert "Inspect" in text
        assert "Challenge" in text
        assert "Validate" in text
        assert "Export" in text
        assert "47%" in text
        assert "Type a number" in text

    def test_format_sub_menu_contains_options(self):
        sub_menu = {
            "workspace": "feed",
            "options": [
                {"key": "A", "label": "Paste raw text"},
                {"key": "B", "label": "Correction"},
            ],
            "context_stats": {},
            "hint": "Sections starving: Revenue",
        }
        text = format_sub_menu_as_text(sub_menu)
        assert "[A] Paste raw text" in text
        assert "[B] Correction" in text
        assert "Sections starving: Revenue" in text
        assert "back" in text
