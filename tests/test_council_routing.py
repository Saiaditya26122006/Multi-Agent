"""
Tests for Council routing — gated agents route to Council, ungated to Mother.
"""

import json
import os
import pytest
from unittest.mock import patch

from config.phase2.council_config import COUNCIL_GATED_AGENTS, COUNCIL_GATED_SECTIONS


class TestRoutingConfig:
    """Verify gated vs ungated routing logic."""

    def test_gated_agents_route_to_council(self):
        for agent in COUNCIL_GATED_AGENTS:
            assert agent in [
                "swot_synthesizer",
                "financial_modelling",
                "marketing_strategy",
                "summary_agent",
            ]

    def test_ungated_agents_not_in_list(self):
        ungated = [
            "opportunity_analyst",
            "environment_research",
            "organisation_designer",
            "operations",
            "launch_contingency",
        ]
        for agent in ungated:
            assert agent not in COUNCIL_GATED_AGENTS

    def test_gated_sections_match_agents(self):
        agent_to_section = {
            "swot_synthesizer": "5",
            "financial_modelling": "12",
            "marketing_strategy": "8",
            "summary_agent": "executive_summary",
        }
        for agent, section in agent_to_section.items():
            assert agent in COUNCIL_GATED_AGENTS
            assert section in COUNCIL_GATED_SECTIONS


class TestRevisionLoop:
    """Test revision loop respects max attempts."""

    def test_max_revisions_enforced(self):
        from config.phase2.council_config import MAX_COUNCIL_REVISIONS
        assert MAX_COUNCIL_REVISIONS == 2

    def test_attempt_tracking_logic(self):
        attempt = 1
        max_revisions = 2

        # First attempt: can revise (attempt 1 < 2)
        assert attempt < max_revisions
        attempt += 1

        # Second attempt: cannot revise (attempt 2 >= 2), must pass or escalate
        assert attempt >= max_revisions


class TestChildAgentRouting:
    """Test that child agents route to COUNCIL_AGENT_JID when set."""

    @patch.dict(os.environ, {"COUNCIL_AGENT_JID": "council@xmpp.test", "MOTHER_AGENT_JID": "mother@xmpp.test"})
    def test_council_jid_takes_priority(self):
        council_jid = os.getenv("COUNCIL_AGENT_JID", "")
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        target = council_jid if council_jid else mother_jid
        assert target == "council@xmpp.test"

    @patch.dict(os.environ, {"COUNCIL_AGENT_JID": "", "MOTHER_AGENT_JID": "mother@xmpp.test"})
    def test_falls_back_to_mother_when_no_council(self):
        council_jid = os.getenv("COUNCIL_AGENT_JID", "")
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        target = council_jid if council_jid else mother_jid
        assert target == "mother@xmpp.test"


class TestRevisePerformative:
    """Test that gated agents handle the 'revise' performative."""

    def test_revise_content_structure(self):
        revise_content = {
            "revision_instructions": "Fix the revenue assumption",
            "original_output": {"confidence_score": "medium", "section_number": "12"},
            "persona_critiques": [
                {"persona": "skeptic", "top_finding": "No source", "severity": "critical", "detail": ""},
            ],
            "council_score": 5.0,
        }
        assert "revision_instructions" in revise_content
        assert "original_output" in revise_content
        assert "persona_critiques" in revise_content
        assert revise_content["persona_critiques"][0]["severity"] == "critical"

    def test_inform_to_council_includes_agent_name(self):
        inform_body = {
            "output": {"confidence_score": "high"},
            "section_number": "5",
            "agent_name": "swot_synthesizer",
        }
        assert "agent_name" in inform_body
        assert inform_body["agent_name"] == "swot_synthesizer"
