"""Tests for the VALIDATE workspace handler."""

import pytest
from unittest.mock import patch, MagicMock

from web.handlers.validate_handler import (
    confirm_assumption,
    kill_assumption,
    report_conversation,
    update_decision,
    get_cascade_preview,
    get_assumption_queue,
    format_validate_response,
)


class TestConfirmAssumption:
    @patch("services.rag_service.store", return_value="chunk_123")
    @patch("services.rag_service.retrieve", return_value=[])
    @patch("web.handlers.validate_handler.get_cascade_preview")
    def test_confirms_successfully(self, mock_cascade, mock_retrieve, mock_store):
        mock_cascade.return_value = {"affected_count": 3, "affected_sections": ["9"], "impact_level": "medium"}

        result = confirm_assumption(
            assumption_text="Pricing is per-department",
            evidence="Customer confirmed in interview",
            source="customer_interview",
        )
        assert result["success"] is True
        assert result["new_status"] == "CONFIRMED"
        assert "3 downstream" in result["message"]


class TestKillAssumption:
    @patch("services.rag_service.store", return_value="chunk_456")
    @patch("web.handlers.validate_handler.get_cascade_preview")
    def test_kills_with_warning_on_high_impact(self, mock_cascade, mock_store):
        mock_cascade.return_value = {"affected_count": 5, "affected_sections": ["9", "12"], "impact_level": "high"}

        result = kill_assumption(
            assumption_text="MVP ready in 6 months",
            reason="No artifact exists",
        )
        assert result["success"] is True
        assert result["new_status"] == "KILLED"
        assert result["warning"] is not None
        assert "5 downstream" in result["warning"]

    @patch("services.rag_service.store", return_value="chunk_789")
    @patch("web.handlers.validate_handler.get_cascade_preview")
    def test_kills_without_warning_on_low_impact(self, mock_cascade, mock_store):
        mock_cascade.return_value = {"affected_count": 1, "affected_sections": ["9"], "impact_level": "low"}

        result = kill_assumption(
            assumption_text="Minor detail",
            reason="Wrong",
        )
        assert result["success"] is True
        assert result["warning"] is None


class TestReportConversation:
    @patch("services.rag_service.store", return_value="chunk_conv")
    def test_stores_conversation(self, mock_store):
        result = report_conversation(
            summary="Discussed pricing",
            who="IESE Research Dean",
            outcome="Confirmed interest but no budget discussion",
        )
        assert result["success"] is True
        assert result["who"] == "IESE Research Dean"
        assert "CONFIRMED evidence" in result["message"]


class TestUpdateDecision:
    @patch("services.conversation_store.store_correction", return_value="new_id")
    def test_updates_decision(self, mock_correction):
        result = update_decision(
            original_decision="Yes to institutional pricing",
            new_decision="Adjust — try per-researcher first",
            reason="Customer feedback suggests lower barrier",
        )
        assert result["success"] is True
        assert result["new_decision"] == "Adjust — try per-researcher first"


class TestCascadePreview:
    @patch("services.rag_service.retrieve")
    def test_computes_affected(self, mock_retrieve):
        chunk1 = MagicMock()
        chunk1.section = "9"
        chunk2 = MagicMock()
        chunk2.section = "12"
        chunk3 = MagicMock()
        chunk3.section = "9"
        mock_retrieve.return_value = [chunk1, chunk2, chunk3]

        result = get_cascade_preview("Pricing assumption")
        assert result["affected_count"] == 3
        assert "9" in result["affected_sections"]
        assert "12" in result["affected_sections"]
        assert result["impact_level"] == "medium"

    @patch("services.rag_service.retrieve", return_value=[])
    def test_no_cascade(self, mock_retrieve):
        result = get_cascade_preview("Isolated fact")
        assert result["affected_count"] == 0
        assert result["impact_level"] == "low"


class TestGetAssumptionQueue:
    @patch("web.handlers.validate_handler.get_cascade_preview")
    @patch("services.coverage_calculator.get_oldest_assumptions")
    def test_returns_prioritized_queue(self, mock_oldest, mock_cascade):
        mock_oldest.return_value = [
            {"id": "1", "content_preview": "Old assumption", "age_days": 42},
            {"id": "2", "content_preview": "Newer assumption", "age_days": 10},
        ]
        mock_cascade.return_value = {"affected_count": 3, "affected_sections": [], "impact_level": "medium"}

        result = get_assumption_queue()
        assert result["count"] == 2
        assert result["queue"][0]["priority_score"] > result["queue"][1]["priority_score"]


class TestFormatValidateResponse:
    def test_success_with_cascade(self):
        result = {
            "success": True,
            "message": "Assumption confirmed.",
            "cascade_effect": {"affected_count": 3, "affected_sections": ["9", "12"], "impact_level": "medium"},
        }
        text = format_validate_response(result)
        assert "Assumption confirmed" in text
        assert "3 related items" in text

    def test_error_response(self):
        result = {"success": False, "error": "DB connection failed"}
        text = format_validate_response(result)
        assert "Error" in text
        assert "DB connection" in text
