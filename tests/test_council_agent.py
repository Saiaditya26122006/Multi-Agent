"""
Tests for Council Agent — persona reviews, synthesizer logic, verdict paths.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from schemas.outputs.council_agent import (
    CouncilReport,
    CouncilVerdict,
    FullCouncilSummary,
    PersonaCritique,
)
from schemas.inputs.council_agent import CouncilReviewInput
from config.phase2.council_config import (
    COUNCIL_PERSONAS,
    COUNCIL_GATED_AGENTS,
    COUNCIL_GATED_SECTIONS,
    MAX_COUNCIL_REVISIONS,
    SYNTHESIZER_PROMPT,
)


class TestCouncilSchemas:
    """Test Pydantic schemas validate correctly."""

    def test_persona_critique_valid(self):
        critique = PersonaCritique(
            persona="skeptic",
            top_finding="Revenue assumption has no cited source",
            severity="critical",
            detail="The 500 customer claim traces to nothing",
        )
        assert critique.persona == "skeptic"
        assert critique.severity == "critical"

    def test_persona_critique_invalid_persona(self):
        with pytest.raises(Exception):
            PersonaCritique(
                persona="invalid_persona",
                top_finding="test",
                severity="critical",
                detail="",
            )

    def test_council_verdict_pass(self):
        verdict = CouncilVerdict(
            decision="pass",
            score=8.5,
            critical_count=0,
            minor_count=1,
            feedback="",
            improvements=[],
        )
        assert verdict.decision == "pass"
        assert verdict.score == 8.5

    def test_council_verdict_revise(self):
        verdict = CouncilVerdict(
            decision="revise",
            score=5.0,
            critical_count=2,
            minor_count=1,
            feedback="Fix revenue assumptions and add evidence",
            improvements=[],
        )
        assert verdict.decision == "revise"
        assert verdict.critical_count == 2

    def test_council_report_full(self):
        report = CouncilReport(
            section_number="5",
            agent_name="swot_synthesizer",
            attempt=1,
            score=7.5,
            decision="pass",
            critiques=[
                PersonaCritique(persona="skeptic", top_finding="Minor issue", severity="minor", detail="..."),
                PersonaCritique(persona="architect", top_finding="Consistent", severity="none", detail=""),
                PersonaCritique(persona="visionary", top_finding="Could aim higher", severity="minor", detail="..."),
                PersonaCritique(persona="stranger", top_finding="Clear enough", severity="none", detail=""),
                PersonaCritique(persona="operator", top_finding="Feasible", severity="none", detail=""),
            ],
            improvements_made=[],
            revision_instructions=None,
        )
        assert len(report.critiques) == 5
        assert report.decision == "pass"

    def test_full_council_summary(self):
        summary = FullCouncilSummary(
            session_id="sess_123",
            pipeline_run_id="run_456",
            sections_reviewed=[
                CouncilReport(
                    section_number="5", agent_name="swot", attempt=1,
                    score=8.0, decision="pass", critiques=[], improvements_made=[],
                ),
            ],
            overall_quality_score=8.0,
            total_revisions_triggered=0,
            strongest_section="5",
            weakest_section="12",
        )
        assert summary.overall_quality_score == 8.0

    def test_review_input_schema(self):
        inp = CouncilReviewInput(
            section_number="12",
            section_name="Financial Plan",
            agent_name="financial_modelling",
            output={"three_statement_model": {}, "confidence_score": "medium"},
            task_id="task_1",
            session_id="sess_1",
            pipeline_run_id="run_1",
        )
        assert inp.section_number == "12"
        assert inp.is_revision is False


class TestCouncilConfig:
    """Test council configuration is correct."""

    def test_all_personas_present(self):
        # saboteur (P3-1) is defined here but gated at runtime by
        # ENABLE_ADVERSARIAL_PERSONA, so it is present in the dict regardless.
        expected = {"skeptic", "architect", "visionary", "stranger", "operator", "saboteur"}
        assert set(COUNCIL_PERSONAS.keys()) == expected

    def test_persona_has_required_fields(self):
        for key, persona in COUNCIL_PERSONAS.items():
            assert "name" in persona, f"{key} missing name"
            assert "icon" in persona, f"{key} missing icon"
            assert "system_prompt" in persona, f"{key} missing system_prompt"
            assert "user_prompt_template" in persona, f"{key} missing user_prompt_template"

    def test_gated_agents_list(self):
        assert "swot_synthesizer" in COUNCIL_GATED_AGENTS
        assert "financial_modelling" in COUNCIL_GATED_AGENTS
        assert "marketing_strategy" in COUNCIL_GATED_AGENTS
        assert "summary_agent" in COUNCIL_GATED_AGENTS
        assert "opportunity_analyst" not in COUNCIL_GATED_AGENTS

    def test_gated_sections_list(self):
        assert "5" in COUNCIL_GATED_SECTIONS
        assert "12" in COUNCIL_GATED_SECTIONS
        assert "1" not in COUNCIL_GATED_SECTIONS

    def test_max_revisions(self):
        assert MAX_COUNCIL_REVISIONS == 2

    def test_synthesizer_prompt_has_placeholder(self):
        assert "{reviews_json}" in SYNTHESIZER_PROMPT


class TestSynthesizerLogic:
    """Test verdict logic without LLM calls."""

    def test_all_none_severity_passes(self):
        reviews = [
            {"persona": "skeptic", "top_finding": "OK", "severity": "none", "detail": ""},
            {"persona": "architect", "top_finding": "OK", "severity": "none", "detail": ""},
            {"persona": "visionary", "top_finding": "OK", "severity": "none", "detail": ""},
            {"persona": "stranger", "top_finding": "OK", "severity": "none", "detail": ""},
            {"persona": "operator", "top_finding": "OK", "severity": "none", "detail": ""},
        ]
        critical_count = sum(1 for r in reviews if r.get("severity") == "critical")
        minor_count = sum(1 for r in reviews if r.get("severity") == "minor")
        decision = "revise" if critical_count > 0 or minor_count >= 3 else "pass"
        assert decision == "pass"

    def test_one_critical_triggers_revise(self):
        reviews = [
            {"persona": "skeptic", "top_finding": "Bad", "severity": "critical", "detail": ""},
            {"persona": "architect", "top_finding": "OK", "severity": "none", "detail": ""},
            {"persona": "visionary", "top_finding": "OK", "severity": "none", "detail": ""},
            {"persona": "stranger", "top_finding": "OK", "severity": "none", "detail": ""},
            {"persona": "operator", "top_finding": "OK", "severity": "none", "detail": ""},
        ]
        critical_count = sum(1 for r in reviews if r.get("severity") == "critical")
        minor_count = sum(1 for r in reviews if r.get("severity") == "minor")
        decision = "revise" if critical_count > 0 or minor_count >= 3 else "pass"
        assert decision == "revise"

    def test_three_minors_triggers_revise(self):
        reviews = [
            {"persona": "skeptic", "top_finding": "Minor", "severity": "minor", "detail": ""},
            {"persona": "architect", "top_finding": "Minor", "severity": "minor", "detail": ""},
            {"persona": "visionary", "top_finding": "Minor", "severity": "minor", "detail": ""},
            {"persona": "stranger", "top_finding": "OK", "severity": "none", "detail": ""},
            {"persona": "operator", "top_finding": "OK", "severity": "none", "detail": ""},
        ]
        critical_count = sum(1 for r in reviews if r.get("severity") == "critical")
        minor_count = sum(1 for r in reviews if r.get("severity") == "minor")
        decision = "revise" if critical_count > 0 or minor_count >= 3 else "pass"
        assert decision == "revise"

    def test_two_minors_passes(self):
        reviews = [
            {"persona": "skeptic", "top_finding": "Minor", "severity": "minor", "detail": ""},
            {"persona": "architect", "top_finding": "Minor", "severity": "minor", "detail": ""},
            {"persona": "visionary", "top_finding": "OK", "severity": "none", "detail": ""},
            {"persona": "stranger", "top_finding": "OK", "severity": "none", "detail": ""},
            {"persona": "operator", "top_finding": "OK", "severity": "none", "detail": ""},
        ]
        critical_count = sum(1 for r in reviews if r.get("severity") == "critical")
        minor_count = sum(1 for r in reviews if r.get("severity") == "minor")
        decision = "revise" if critical_count > 0 or minor_count >= 3 else "pass"
        assert decision == "pass"

    def test_score_calculation(self):
        critical_count = 1
        minor_count = 2
        score = 10.0 - (critical_count * 2) - (minor_count * 0.5)
        assert score == 7.0
