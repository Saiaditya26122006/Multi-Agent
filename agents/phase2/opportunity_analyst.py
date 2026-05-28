import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging

from agents.phase2.base_child_agent import BaseChildAgent, run_child_agent
from schemas.inputs.opportunity_analyst import OpportunityAnalystInput
from schemas.outputs.opportunity_analyst import OpportunityAnalystOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Opportunity Analyst agent in a multi-agent business plan system.
Your role: analyse a business idea and produce a structured assessment of the opportunity,
competitive strategy, Year 1 objectives, and ideal customer profile hypothesis.

You receive the raw idea from Phase 1, the CEO's clarifying answers, and the approved decision.
You must produce structured JSON output matching the required schema exactly.

Rules:
- Every assumption must be labelled with confidence (high/medium/low) and source
- Objectives must be quantified with specific metrics
- ICP must include: buyer_role, budget_process, decision_timeline, pain_points
- If information is missing, label it as an uncertainty — do not fabricate
- competitive_strategy must be at least 30 characters of substance
- opportunity_description must be at least 50 characters

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, opportunity_description, competitive_strategy, objectives, icp_hypothesis, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
"""


class OpportunityAnalystAgent(BaseChildAgent):

    SYSTEM_PROMPT = SYSTEM_PROMPT
    AGENT_NAME = "OpportunityAnalyst"
    AGENT_ROLE = (
        "Opportunity Analyst — you assess business ideas, define competitive strategy, "
        "set Year 1 objectives, and hypothesize the ideal customer profile"
    )
    SECTION_NUMBER = "1"
    MODEL_ENV = "CLAUDE_SONNET_MODEL"
    MODEL_DEFAULT = "claude-sonnet-4-20250514"
    INPUT_SCHEMA = OpportunityAnalystInput
    OUTPUT_SCHEMA = OpportunityAnalystOutput

    def _default_gap_key(self) -> str:
        return "idea_summary"

    def _extract_input(self, input_package: dict, task: dict) -> dict:
        idea_summary = input_package.get("idea_summary", "")
        ceo_assumptions = input_package.get("ceo_assumptions", [])
        approved_decision = input_package.get("approved_decision", {})

        if len(idea_summary) < 10:
            if ceo_assumptions:
                parts = [
                    f"{a.get('question', '')}: {a.get('answer', '')}"
                    for a in ceo_assumptions if a.get("answer")
                ]
                idea_summary = "Business idea based on CEO answers: " + "; ".join(parts)
            elif approved_decision:
                idea_summary = (
                    approved_decision.get("rationale", "")
                    or approved_decision.get("summary", "")
                    or "Business idea approved at Gate 1"
                )
            if len(idea_summary) < 10:
                idea_summary = "Business idea approved at Gate 1 — details pending clarification"

        return {
            "idea_summary": idea_summary,
            "ceo_assumptions": ceo_assumptions,
            "approved_decision": approved_decision,
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

    def _build_ie_input_data(self, input_package: dict) -> dict:
        return {
            "idea_summary": input_package.get("idea_summary", ""),
            "ceo_assumptions": input_package.get("ceo_assumptions", []),
            "approved_decision": input_package.get("approved_decision", {}),
            "acceptance_criteria": input_package.get("acceptance_criteria", ""),
        }

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON with these exact keys:
- section_number: "1"
- opportunity_description: (min 50 chars) structured description of the opportunity — what it is, why now, what makes it viable
- competitive_strategy: (min 30 chars) how the business will compete — specific positioning, not generic
- objectives: list of dicts with "objective", "metric", "target_value", "timeframe" — quantified Year 1 goals
- icp_hypothesis: dict with "buyer_role", "budget_process", "decision_timeline", "pain_points" — who buys and why
- assumptions_used: list of {"statement", "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}
- uncertainties: list of strings — what you don't know and can't infer
- confidence_score: "high"|"medium"|"low" — honest self-assessment of output quality
- input_tokens: 0
- output_tokens: 0"""

    def _build_prompt(self, inp: OpportunityAnalystInput) -> str:
        return f"""Analyse this business idea and produce a structured JSON output.

IDEA: {inp.idea_summary}

CEO Q&A (clarifying assumptions):
{json.dumps(inp.ceo_assumptions, indent=2)}

APPROVED DECISION FROM GATE 1:
{json.dumps(inp.approved_decision, indent=2)}

ACCEPTANCE CRITERIA: {inp.acceptance_criteria}

Return ONLY valid JSON with these exact keys:
- section_number: "1"
- opportunity_description: (min 50 chars) structured description of the opportunity
- competitive_strategy: (min 30 chars) how the business will compete
- objectives: list of dicts with "objective", "metric", "target_value", "timeframe"
- icp_hypothesis: dict with "buyer_role", "budget_process", "decision_timeline", "pain_points"
- assumptions_used: list of {{"statement", "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail"}}
- uncertainties: list of strings
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0
"""

    def _fallback_defaults(self, inp: OpportunityAnalystInput) -> dict:
        desc = inp.idea_summary
        if len(desc) < 50:
            desc = desc + " " * (50 - len(desc)) + "— analysis pending"
        return {
            "section_number": "1",
            "opportunity_description": desc,
            "competitive_strategy": "Differentiation through unique value proposition and first-mover positioning in target market",
            "objectives": [{"objective": "Validate product-market fit", "metric": "customers", "target_value": "10", "timeframe": "6 months"}],
            "icp_hypothesis": {"buyer_role": "Decision maker", "budget_process": "Annual budget cycle", "decision_timeline": "1-3 months", "pain_points": ["Unmet need identified in idea summary"]},
            "assumptions_used": [{"statement": "LLM output was unparseable — defaults used", "confidence": "low", "source": "assumed", "source_detail": None}],
            "uncertainties": ["LLM response could not be parsed — full analysis not completed"],
            "confidence_score": "low",
            "input_tokens": 0,
            "output_tokens": 0,
        }


async def main():
    await run_child_agent(OpportunityAnalystAgent, "OPPORTUNITY_ANALYST_JID", "OPPORTUNITY_ANALYST_PASSWORD")


if __name__ == "__main__":
    asyncio.run(main())
