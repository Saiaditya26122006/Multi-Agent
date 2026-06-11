import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging

from agents.phase2.base_child_agent import BaseChildAgent, run_child_agent
from schemas.inputs.alliances import AlliancesInput
from schemas.outputs.alliances import AlliancesOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Alliances & Outsourcing agent in a multi-agent business plan system.
Your role: design partnership strategy, determine make-vs-buy decisions, identify strategic alliances, and assess outsourcing opportunities for partnership-heavy or platform business models.

## REASONING FRAMEWORK:

1. PARTNERSHIP VALUE EXCHANGE (Both sides must win)
   - What does the partner GET? (revenue share, customer access, product integration, brand association)
   - What do WE GET? (distribution, credibility, technology, capital efficiency)
   - If value exchange is one-sided, partnership will fail
   - NEVER write "strategic partnership" without stating what each side gains

2. MAKE VS BUY (Focus beats feature creep)
   - **Make in-house**: Core differentiator, IP-sensitive, high strategic value
   - **Buy/outsource**: Commodity functions, non-core, proven solutions exist
   - Examples: Build your unique algorithm, buy authentication (Auth0), outsource customer support (offshore)
   - NEVER write "build everything" for early-stage startup — capital and time constraints demand focus

3. CRITICALITY ASSESSMENT
   - **Critical**: Business cannot launch or operate without this partnership
   - **Important**: Significantly accelerates GTM or reduces cost/risk
   - **Nice-to-have**: Incremental benefit, can be deferred
   - If >3 partnerships are "critical", that is fragile — too many dependencies

4. PARTNERSHIP RISKS
   - **Dependency risk**: Partner controls critical path (e.g., sole data provider)
   - **Execution risk**: Partner delays or delivers poor quality
   - **Control risk**: Partner owns customer relationship, can cut you out
   - **Economic risk**: Revenue share or royalty erodes unit economics
   - State mitigation: "Mitigate with backup provider" or "Mitigate with contractual SLA"

## ANTI-PATTERNS:
- NEVER write "strategic partnerships with leading companies" without naming them
- NEVER write "explore partnership opportunities" — be specific about WHO and WHEN
- NEVER write "outsource non-core functions" without listing which functions
- NEVER claim partnerships are "low-risk" — all partnerships have execution risk

## RULES:
- alliance_plan must have at least 1 partnership with partner_type, rationale, value_exchange, timeline, criticality
- outsourcing_strategy must be at least 100 characters — state what is outsourced and why
- partnership_risks must list specific risks, not generic

You must respond with ONLY valid JSON. The JSON must contain: section_number, alliance_plan, outsourcing_strategy, partnership_risks, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
"""


class AlliancesAgent(BaseChildAgent):

    SYSTEM_PROMPT = SYSTEM_PROMPT
    AGENT_NAME = "Alliances"
    AGENT_ROLE = "Alliances & Outsourcing — partnership strategy, make-vs-buy decisions"
    SECTION_NUMBER = "7"
    MODEL_ENV = "CLAUDE_HAIKU_MODEL"
    MODEL_DEFAULT = "claude-haiku-4-5-20251001"
    INPUT_SCHEMA = AlliancesInput
    OUTPUT_SCHEMA = AlliancesOutput

    def _default_gap_key(self) -> str:
        return "competitive_strategy"

    def _extract_input(self, input_package: dict, task: dict) -> dict:
        return {
            "competitive_strategy": input_package.get("competitive_strategy", ""),
            "opportunity_description": input_package.get("opportunity_description", ""),
            "strategic_implications": input_package.get("strategic_implications", ""),
            "weaknesses": input_package.get("weaknesses", []),
            "partnership_targets": input_package.get("partnership_targets", ""),
            "outsourcing_strategy": input_package.get("outsourcing_strategy", ""),
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

    def _build_ie_input_data(self, input_package: dict) -> dict:
        return self._extract_input(input_package, {})

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON:
- section_number: "7"
- alliance_plan: [{"partner_type": str, "rationale": str (min 30 chars), "value_exchange": str (min 20 chars), "timeline": str, "criticality": "critical"|"important"|"nice_to_have"}]
- outsourcing_strategy: str (min 100 chars)
- partnership_risks: [str]
- assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens"""

    def _build_prompt(self, inp: AlliancesInput) -> str:
        return f"""Design alliances and outsourcing strategy.

COMPETITIVE STRATEGY: {inp.competitive_strategy}
OPPORTUNITY: {inp.opportunity_description}
STRATEGIC IMPLICATIONS: {inp.strategic_implications or "Not provided"}

Return JSON with: section_number, alliance_plan, outsourcing_strategy, partnership_risks, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens
"""

    def _fallback_defaults(self, inp: AlliancesInput) -> dict:
        return {
            "section_number": "7",
            "alliance_plan": [{
                "partner_type": "Distribution partner",
                "rationale": "Accelerates customer acquisition by leveraging existing customer base",
                "value_exchange": "We provide technology integration, partner provides customer access and credibility",
                "timeline": "Establish within first 6 months",
                "criticality": "important"
            }],
            "outsourcing_strategy": "Outsource non-core functions: customer support (offshore team), design (contractors), legal/compliance (external counsel). Build in-house: core product, sales, customer success. Rationale: focus limited resources on differentiation.",
            "partnership_risks": ["Partner execution delays", "Dependency on single partner"],
            "assumptions_used": [{"statement": "Fallback used", "confidence": "low", "source": "assumed", "source_detail": None}],
            "uncertainties": ["Partnership details incomplete"],
            "confidence_score": "low",
            "input_tokens": 0,
            "output_tokens": 0,
        }


async def main():
    await run_child_agent(AlliancesAgent, "ALLIANCES_JID", "ALLIANCES_PASSWORD")


if __name__ == "__main__":
    asyncio.run(main())
