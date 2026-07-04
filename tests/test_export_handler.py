"""Tests for the EXPORT workspace handler."""

import pytest
from unittest.mock import patch, MagicMock

from web.handlers.export_handler import (
    export_executive_summary,
    export_investor_version,
    export_internal_version,
    export_gap_report,
    get_export_readiness,
    format_export_response,
)


class TestExportInvestorVersion:
    @patch("services.rag_service._get_supabase")
    @patch("web.handlers.export_handler.get_export_readiness")
    def test_blocks_below_threshold(self, mock_readiness, mock_supabase):
        mock_readiness.return_value = {"coverage_pct": 20, "warnings": []}

        result = export_investor_version()
        assert result["success"] is False
        assert "20%" in result["message"]

    @patch("services.rag_service._get_supabase")
    @patch("web.handlers.export_handler.get_export_readiness")
    def test_generates_with_hidden_count(self, mock_readiness, mock_supabase):
        mock_readiness.return_value = {"coverage_pct": 60, "warnings": []}
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.is_.return_value.execute.return_value = MagicMock(
            data=[{"id": "1"}, {"id": "2"}, {"id": "3"}]
        )
        mock_supabase.return_value = mock_client

        result = export_investor_version()
        assert result["success"] is True
        assert result["hidden_count"] == 3


class TestExportInternalVersion:
    @patch("services.coverage_calculator.get_confidence_breakdown")
    def test_includes_all_tags(self, mock_conf):
        mock_conf.return_value = {"breakdown": {"CONFIRMED": 5}, "confidence_pct": 50}

        result = export_internal_version()
        assert result["success"] is True
        assert result["includes"]["epistemic_tags"] is True
        assert result["includes"]["contradiction_flags"] is True


class TestExportGapReport:
    @patch("services.coverage_calculator.get_blocked_sections", return_value=[])
    @patch("services.coverage_calculator.get_stale_items", return_value=[])
    @patch("services.coverage_calculator.get_oldest_assumptions", return_value=[])
    @patch("services.coverage_calculator.get_plan_coverage")
    def test_submission_ready(self, mock_coverage, mock_oldest, mock_stale, mock_blocked):
        mock_coverage.return_value = {
            "coverage_pct": 80,
            "per_section": {
                "BP.1": {"coverage_pct": 90, "title": "Product"},
            },
        }
        result = export_gap_report()
        assert result["success"] is True
        assert result["submission_ready"] is True

    @patch("services.coverage_calculator.get_blocked_sections")
    @patch("services.coverage_calculator.get_stale_items", return_value=[])
    @patch("services.coverage_calculator.get_oldest_assumptions")
    @patch("services.coverage_calculator.get_plan_coverage")
    def test_not_ready_with_gaps(self, mock_coverage, mock_oldest, mock_stale, mock_blocked):
        mock_coverage.return_value = {
            "coverage_pct": 30,
            "per_section": {
                "BP.9": {"coverage_pct": 10, "title": "Revenue"},
            },
        }
        mock_oldest.return_value = [
            {"content_preview": "Old thing", "age_days": 50},
        ]
        mock_blocked.return_value = [
            {"section_id": "BP.12", "blocked_by": ["BP.9"]},
        ]

        result = export_gap_report()
        assert result["success"] is True
        assert result["submission_ready"] is False
        assert result["gap_count"] > 0


class TestExportReadiness:
    @patch("services.coverage_calculator.get_blocked_sections", return_value=[])
    @patch("services.coverage_calculator.get_dashboard_stats")
    def test_ready_when_high_coverage(self, mock_stats, mock_blocked):
        mock_stats.return_value = {
            "coverage_pct": 70,
            "contradiction_count": 0,
            "stale_count": 0,
        }
        result = get_export_readiness()
        assert result["ready"] is True
        assert result["warnings"] == []

    @patch("services.coverage_calculator.get_blocked_sections")
    @patch("services.coverage_calculator.get_dashboard_stats")
    def test_not_ready_with_warnings(self, mock_stats, mock_blocked):
        mock_stats.return_value = {
            "coverage_pct": 25,
            "contradiction_count": 3,
            "stale_count": 5,
        }
        mock_blocked.return_value = [{"section_id": "BP.9", "blocked_by": ["BP.5"]}]

        result = get_export_readiness()
        assert result["ready"] is False
        assert len(result["warnings"]) >= 2


class TestFormatExportResponse:
    def test_format_success(self):
        result = {
            "success": True,
            "format": "full_plan",
            "message": "Plan exported.",
            "warnings": ["Low coverage"],
        }
        text = format_export_response(result)
        assert "Plan exported" in text
        assert "Low coverage" in text

    def test_format_failure(self):
        result = {"success": False, "error": "DB connection failed"}
        text = format_export_response(result)
        assert "failed" in text.lower()
        assert "DB connection" in text

    def test_format_gap_report(self):
        result = {
            "success": True,
            "format": "gap_report",
            "message": "NOT ready. 2 gaps.",
            "gaps": [
                {"type": "empty_section", "section": "BP.9", "coverage": 10, "severity": "critical"},
                {"type": "blocked_section", "section": "BP.12", "blocked_by": ["BP.9"], "severity": "high"},
            ],
        }
        text = format_export_response(result)
        assert "BP.9" in text
        assert "BP.12" in text
        assert "CRITICAL" in text
