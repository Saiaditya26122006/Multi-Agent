"""Tests for the INSPECT workspace handler."""

import pytest
from unittest.mock import patch, MagicMock

from web.handlers.inspect_handler import (
    get_coverage_heatmap,
    get_confidence_breakdown,
    get_contradictions_list,
    get_stale_data_report,
    get_dependency_view,
    answer_inspect_question,
    format_inspect_response,
)


class TestCoverageHeatmap:
    @patch("services.coverage_calculator.get_plan_coverage")
    def test_returns_sections(self, mock_coverage):
        mock_coverage.return_value = {
            "coverage_pct": 50.0,
            "total_nodes": 20,
            "filled_nodes": 10,
            "per_section": {
                "BP.1": {"title": "Product", "total_nodes": 5, "filled_nodes": 4, "coverage_pct": 80},
                "BP.9": {"title": "Revenue", "total_nodes": 10, "filled_nodes": 2, "coverage_pct": 20},
            },
        }
        result = get_coverage_heatmap()
        assert result["overall_coverage_pct"] == 50.0
        assert len(result["sections"]) == 2
        assert result["sections"][0]["status"] == "strong"
        assert result["sections"][1]["status"] == "weak"


class TestConfidenceBreakdown:
    @patch("services.coverage_calculator.get_confidence_breakdown")
    def test_returns_breakdown(self, mock_conf):
        mock_conf.return_value = {
            "breakdown": {"CONFIRMED": 5, "ASSUMPTION": 10},
            "confidence_pct": 33.3,
            "total_tagged": 15,
            "confirmed_count": 5,
        }
        result = get_confidence_breakdown()
        assert result["confidence_pct"] == 33.3


class TestStaleDataReport:
    @patch("services.coverage_calculator.get_stale_items")
    def test_returns_stale_items(self, mock_stale):
        mock_stale.return_value = [
            {"id": "1", "content_preview": "Old fact", "source_type": "ceo_doc", "age_days": 45},
        ]
        result = get_stale_data_report()
        assert result["count"] == 1
        assert result["items"][0]["age_days"] == 45


class TestDependencyView:
    @patch("services.coverage_calculator.get_blocked_sections")
    @patch("services.coverage_calculator.get_sections")
    def test_returns_nodes_and_edges(self, mock_sections, mock_blocked):
        mock_sections.return_value = {
            "BP.1": {"title": "Product"},
            "BP.9": {"title": "Revenue"},
        }
        mock_blocked.return_value = [
            {"section_id": "BP.9", "blocked_by": ["BP.1"]},
        ]
        result = get_dependency_view()
        assert len(result["nodes"]) == 2
        assert result["blocked_count"] == 1
        assert any(e["from"] == "BP.1" and e["to"] == "BP.9" for e in result["edges"])


class TestAnswerInspectQuestion:
    @patch("services.rag_service.retrieve")
    def test_returns_sources(self, mock_retrieve):
        mock_chunk = MagicMock()
        mock_chunk.content = "Pricing is SaaS per department"
        mock_chunk.source_type = "ceo_doc"
        mock_chunk.section = "9"
        mock_chunk.epistemic_status = "ASSUMPTION"
        mock_chunk.similarity = 0.72
        mock_retrieve.return_value = [mock_chunk]

        result = answer_inspect_question("What's the pricing model?")
        assert len(result["sources"]) == 1
        assert result["sources"][0]["section"] == "9"

    @patch("services.rag_service.retrieve", return_value=[])
    def test_no_data_found(self, mock_retrieve):
        result = answer_inspect_question("What about unicorns?")
        assert "No relevant data" in result["answer"]


class TestFormatInspectResponse:
    def test_coverage_format(self):
        data = {
            "overall_coverage_pct": 50,
            "sections": [
                {"section_id": "BP.1", "coverage_pct": 80, "filled_nodes": 4, "total_nodes": 5, "status": "strong"},
                {"section_id": "BP.9", "coverage_pct": 20, "filled_nodes": 2, "total_nodes": 10, "status": "weak"},
            ],
        }
        text = format_inspect_response(data, "coverage")
        assert "50%" in text
        assert "BP.1" in text
        assert "BP.9" in text

    def test_contradictions_format_empty(self):
        data = {"count": 0, "contradictions": []}
        text = format_inspect_response(data, "contradictions")
        assert "No unresolved" in text

    def test_stale_format(self):
        data = {
            "count": 2,
            "max_age_threshold": 30,
            "items": [
                {"age_days": 45, "content_preview": "Old pricing info"},
                {"age_days": 35, "content_preview": "Stale competitor data"},
            ],
        }
        text = format_inspect_response(data, "stale")
        assert "2 stale" in text
        assert "45d" in text
