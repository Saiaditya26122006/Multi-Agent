import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging
from datetime import datetime

from agents.phase2.base_child_agent import BaseChildAgent, run_child_agent
from schemas.inputs.opportunity_analyst import OpportunityAnalystInput
from schemas.outputs.opportunity_analyst import OpportunityAnalystOutput
from services.search_service import search_for_section

logger = logging.getLogger(__name__)

SEARCH_QUERIES = {
    "1": [
        "academic manuscript validation software market size 2025",
        "epistemic validation tools universities Europe market",
        "pre-submission research diagnostics SaaS competitors"
    ]
}


def _get_live_market_data(section_number: str) -> str:
    """Run section-specific queries and format results for prompt injection."""
    queries = SEARCH_QUERIES.get(section_number, [])
    if not queries:
        return ""

    all_results = []
    for query in queries:
        results = search_for_section(section_number, query)
        all_results.extend(results)

    if not all_results:
        return "No live market data retrieved for this section."

    lines = [f"Retrieved {datetime.utcnow().strftime('%Y-%m-%d')}:"]
    for i, r in enumerate(all_results[:8], 1):
        lines.append(
            f"[{i}] {r['title']} — {r['snippet'][:200]} "
            f"(Source: {r['url']}, Freshness: {r['freshness']})"
        )
    return "\n".join(lines)

SYSTEM_PROMPT = """You are the Opportunity Analyst agent in a multi-agent business plan system.
Your role: rigorously assess a business idea and produce a structured, evidence-backed evaluation
of the opportunity, competitive strategy, Year 1 objectives, and ideal customer profile hypothesis.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. TAM-SAM-SOM MARKET SIZING (No vague "large market")
   - TAM (Total Addressable Market): The global/regional market for the broad category. Example: "Global SaaS market: $200B"
   - SAM (Serviceable Addressable Market): TAM filtered by geography, segment, and budget. Example: "Academic software in Europe: $2B"
   - SOM (Serviceable Obtainable Market): Your realistic capture of SAM in Year 1 and Year 3. Example: "Year 1: $500K (0.025% of SAM)"
   - CRITICAL RULE: If capture_rate_year_1 > 5% of SAM, you MUST justify why. Startups typically capture 0.1-5% in Year 1.
   - TAM must cite a source (market research URL, industry report, or calculation shown explicitly).
   - SAM must show the filtering logic: "TAM × [geography filter] × [segment filter] × [budget filter]"
   - SOM must explain constraints: sales capacity, brand awareness, competition, distribution reach.

2. MARKET TIMING (Why now?)
   - What structural shift (regulatory, technological, behavioural) makes this possible TODAY that was not possible 3 years ago?
   - If no clear timing catalyst exists, the opportunity_description must explicitly say so — this is a yellow flag.

3. DEFENSIBILITY (What stops copying?)
   - Identify the moat: network effects, switching costs, proprietary data, regulatory barriers, or economies of scale.
   - If the only moat is "execution speed," say that plainly — it is a weak moat and must be labelled as such.
   - NEVER write "first-mover advantage" as a standalone strategy. First-mover means nothing without a lock-in mechanism.

4. ICP VALIDATION (Who actually pays?)
   - The buyer_role must be someone who can sign a cheque, not just someone who has the problem.
   - budget_process must reflect how that buyer actually purchases (annual cycle, project-based, discretionary).
   - decision_timeline must be realistic for the buyer's org size. Enterprise = 6-12 months. SMB = 1-4 weeks.
   - pain_points must be problems the buyer would describe in their own words, not abstract business language.

5. ASSUMPTION AUDIT
   - For every assumption you make, ask: "What evidence would disprove this?" If you cannot name the evidence, confidence = "low".
   - Assumptions sourced from CEO answers get "alex_provided". Assumptions you infer get "agent_inferred".
   - If you are inferring more than 3 assumptions, confidence_score for the entire output must be "medium" or "low".

6. KILL TEST
   - Ask: "Under what conditions should this idea be abandoned?" State this explicitly in uncertainties.
   - If the idea depends on a single unvalidated assumption with no fallback, flag it prominently.

## ANTI-PATTERNS — If you catch yourself writing any of these, stop and get more specific:
- NEVER write "leverage technology to disrupt the market." Name the specific technology and specific disruption mechanism.
- NEVER write "large and growing market" without a number or a named sizing source.
- NEVER write "unique value proposition" without explaining what makes it unique and for whom.
- NEVER write "strong team with relevant experience" — cite what experience and why it maps to this problem.
- NEVER write "multiple revenue streams" without naming each stream and its contribution logic.

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If the idea_summary is under 20 characters AND no CEO Q&A or approved_decision provides substance, output confidence_score = "low" and add "FATAL: Insufficient information to assess opportunity" to uncertainties.
- If the idea has zero identifiable customer and zero revenue mechanism, flag as FATAL.

## Rules:
- Every assumption must be labelled with confidence (high/medium/low) and source
- Objectives must be quantified with specific metrics — no vanity metrics (followers, impressions)
- ICP must include: buyer_role, budget_process, decision_timeline, pain_points
- If information is missing, label it as an uncertainty — do not fabricate
- competitive_strategy must be at least 30 characters of substance
- opportunity_description must be at least 50 characters
- market_sizing MUST include all TAM-SAM-SOM fields with sources and calculation logic

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, opportunity_description, competitive_strategy, market_sizing (with tam, tam_definition, tam_source, sam, sam_definition, sam_calculation, som_year_1, som_year_3, som_logic, capture_rate_year_1_pct, capture_rate_year_3_pct), objectives, icp_hypothesis, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
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
            "live_market_data": _get_live_market_data("1"),
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
            # market_sizing is required by the schema. Values are deliberately
            # unusable placeholders — a plausible-looking TAM here would be read as
            # a real estimate. The zero capture rates and the text say otherwise.
            "market_sizing": {
                "tam": 1.0,
                "tam_definition": "UNKNOWN — no market sizing was produced because the LLM output could not be parsed",
                "tam_source": "No source — placeholder value, not an estimate",
                "sam": 1.0,
                "sam_definition": "UNKNOWN — no market sizing was produced because the LLM output could not be parsed",
                "sam_calculation": "No calculation — placeholder value, not an estimate",
                "som_year_1": 1.0,
                "som_year_3": 1.0,
                "som_logic": "No logic available — this section fell back to defaults after an unparseable LLM response. These figures are placeholders and must not be used.",
                "capture_rate_year_1_pct": 0.0,
                "capture_rate_year_3_pct": 0.0,
            },
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
