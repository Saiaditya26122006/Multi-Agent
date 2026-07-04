"""Tests for the CHALLENGE workspace handler."""

import pytest
from unittest.mock import patch, MagicMock

from web.handlers.challenge_handler import (
    challenge_weakest_assumptions,
    challenge_claim,
    compare_competitor,
    get_vulnerability_list,
    format_challenge_response,
    _generate_challenge_text,
    _section_verdict,
    _overall_plan_verdict,
)


class TestChallengeWeakest:
    @patch("services.coverage_calculator.get_oldest_assumptions")
    def test_returns_vulnerabilities(self, mock_oldest):
        mock_oldest.return_value = [
            {"id": "1", "content_preview": "SaaS pricing model", "age_days": 42},
            {"id": "2", "content_preview": "Dean is buyer", "age_days": 38},
        ]
        result = challenge_weakest_assumptions(top_k=2)
        assert result["status"] == "challenges_ready"
        assert len(result["assumptions"]) == 2
        assert result["assumptions"][0]["risk_level"] == "critical"

    @patch("services.coverage_calculator.get_oldest_assumptions", return_value=[])
    def test_no_targets(self, mock_oldest):
        result = challenge_weakest_assumptions()
        assert result["status"] == "no_targets"


class TestChallengeClaim:
    @patch("services.rag_service.retrieve")
    def test_challenges_with_evidence(self, mock_retrieve):
        confirmed_chunk = MagicMock()
        confirmed_chunk.epistemic_status = "CONFIRMED"
        confirmed_chunk.content = "Supporting fact"

        contra_chunk = MagicMock()
        contra_chunk.epistemic_status = "CONTRADICTION"
        contra_chunk.content = "Conflicting fact"

        mock_retrieve.return_value = [confirmed_chunk, contra_chunk]

        result = challenge_claim("Pricing is per-department")
        assert result["status"] == "claim_challenged"
        assert result["supporting_evidence"] == 1
        assert result["contradicting_evidence"] == 1

    @patch("services.rag_service.retrieve", return_value=[])
    def test_no_evidence(self, mock_retrieve):
        result = challenge_claim("Unicorns exist")
        assert result["status"] == "claim_challenged"
        assert result["supporting_evidence"] == 0


class TestCompareCompetitor:
    @patch("services.rag_service.retrieve")
    def test_finds_competitor_data(self, mock_retrieve):
        chunk = MagicMock()
        chunk.content = "Iris.ai uses per-researcher pricing"
        chunk.source_type = "ceo_doc"
        mock_retrieve.return_value = [chunk]

        result = compare_competitor("Iris.ai")
        assert result["status"] == "comparison_ready"
        assert result["data_points"] == 1

    @patch("services.rag_service.retrieve", return_value=[])
    def test_no_competitor_data(self, mock_retrieve):
        result = compare_competitor("Unknown Corp")
        assert result["status"] == "no_data"


class TestVulnerabilityList:
    @patch("services.coverage_calculator.get_stale_items", return_value=[])
    @patch("services.coverage_calculator.get_oldest_assumptions")
    def test_ranked_by_severity(self, mock_oldest, mock_stale):
        mock_oldest.return_value = [
            {"id": "1", "content_preview": "Old claim", "age_days": 50},
            {"id": "2", "content_preview": "Newer claim", "age_days": 15},
        ]
        result = get_vulnerability_list()
        assert result["count"] == 2
        assert result["vulnerabilities"][0]["severity_score"] > result["vulnerabilities"][1]["severity_score"]


class TestVerdicts:
    def test_section_fragile(self):
        assert "FRAGILE" in _section_verdict(8, 2)

    def test_section_solid(self):
        assert "SOLID" in _section_verdict(2, 8)

    def test_section_mixed(self):
        assert "MIXED" in _section_verdict(5, 5)

    def test_section_empty(self):
        assert "EMPTY" in _section_verdict(0, 0)

    def test_overall_high_risk(self):
        risks = [{"verdict": "FRAGILE"}, {"verdict": "FRAGILE"}, {"verdict": "SOLID"}]
        assert "HIGH RISK" in _overall_plan_verdict(risks)


class TestFormatting:
    def test_format_no_targets(self):
        result = {"status": "no_targets", "message": "Nothing to challenge.", "assumptions": []}
        text = format_challenge_response(result)
        assert "Nothing to challenge" in text

    def test_format_challenges_ready(self):
        result = {
            "status": "challenges_ready",
            "message": "Found 1",
            "assumptions": [{
                "claim": "SaaS pricing",
                "age_days": 42,
                "risk_level": "critical",
                "challenge": "What evidence?",
            }],
        }
        text = format_challenge_response(result)
        assert "CRITICAL" in text
        assert "42 days" in text
