import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging

from agents.phase2.base_child_agent import BaseChildAgent, run_child_agent
from schemas.inputs.hr_plan import HRPlanInput
from schemas.outputs.hr_plan import HRPlanOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Human Resources Plan agent in a multi-agent business plan system.
Your role: design the hiring plan, sequence roles by priority, estimate costs, define personnel policy, and identify knowledge gaps the business must close.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. HIRING SEQUENCING (Who is needed WHEN, not just what roles exist)
   - **First hire**: Depends on business model and founder's background
     - B2B SaaS with technical founder → First hire: Sales/BD (Months 0-3)
     - B2B SaaS with sales founder → First hire: Tech Lead (Months 0-3)
     - Consumer product → First hire: Growth/Marketing (Months 3-6)
   - **Critical path**: What role unblocks the most progress?
   - **Sequential dependencies**: Must hire X before Y (e.g., VP Sales before SDRs)
   - NEVER write "hire everyone in Month 6" — hiring is sequential, not parallel

2. COST REALISM (Are salary estimates market-rate for the geography?)
   - **US/UK salaries** (annual, pre-equity):
     - VP/C-level: $150K-$250K
     - Senior IC (eng/sales/ops): $100K-$150K
     - Mid-level IC: $70K-$100K
     - Junior IC: $50K-$70K
   - **EU salaries**: ~70-80% of US rates
   - **Contractors vs FTE**: Contractors 1.5-2x hourly rate but no benefits/equity
   - If revenue assumptions show $0 revenue until Month 6, headcount costs must be covered by runway
   - NEVER claim "VP Sales at $60K" — that is not market rate

3. CAPACITY MATCH (Does sales headcount support revenue targets?)
   - **Sales capacity formula**: (# of reps) x (quota per rep) = total bookings capacity
   - **Typical SaaS rep quota**: $500K-$1M ARR per year (mid-market), $1.5M-$3M (enterprise)
   - If revenue_assumptions show $2M ARR target, you need 2-4 sales reps ramped by Year 1 end
   - **Ramp time**: New sales rep takes 3-6 months to reach full productivity
   - NEVER show $5M revenue target with 1 sales rep — the math doesn't work

4. CAPABILITY CLOSURE (Does the hiring plan close the gaps from Sections 2 and 4?)
   - team_gaps from Section 2 are team-related weaknesses (e.g., "No GTM experience")
   - capability_gaps from Section 4 are organizational capability weaknesses
   - Every gap labeled "critical" must have a corresponding hire in roles_and_responsibilities
   - If a gap is NOT closed by a hire, it must appear in knowledge_gaps with mitigation plan (training, advisory, outsource)

5. COMPENSATION STRATEGY (Equity, salary mix, motivation)
   - **Early-stage equity**: Founding team 60-80%, employee pool 10-20%, investors 10-20%
   - **VP-level equity**: 0.5-2% vesting over 4 years
   - **IC-level equity**: 0.05-0.5% vesting over 4 years
   - **Cash vs equity trade-off**: Below-market salary → higher equity, market salary → lower equity
   - personnel_policy must state: "Market salary + X% equity pool" OR "80% market salary + Y% equity pool"

## ANTI-PATTERNS — If you catch yourself writing any of these, you do not have enough information:
- NEVER write "hire as needed" — every hire must have a month and justification
- NEVER write "competitive salary" without stating a range
- NEVER write "build a strong team" — be specific about which roles
- NEVER write "5 engineers" without specifying: frontend, backend, full-stack, DevOps, etc.
- NEVER write "sales team" without specifying: AE, SDR, CSM, Sales Ops, etc.
- NEVER show 10+ hires in Year 1 for a pre-revenue startup — that is not realistic

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If business_model is empty AND capability_gaps is empty AND team_gaps is empty, escalate with trigger "unclear_input" and gap_key "business_model".

## HEADCOUNT PLAN FORMAT:
The headcount_plan must be a dictionary with monthly granularity for Year 1:
```json
{
  "month_0": {"headcount": 2, "total_cost_monthly": 15000, "roles": ["Founder", "Tech Lead"]},
  "month_3": {"headcount": 3, "total_cost_monthly": 25000, "roles": ["Founder", "Tech Lead", "VP Sales"]},
  "month_6": {"headcount": 4, "total_cost_monthly": 35000, "roles": ["Founder", "Tech Lead", "VP Sales", "Engineer 1"]},
  "month_12": {"headcount": 6, "total_cost_monthly": 50000, "roles": [...]}
}
```
Only include months where headcount or cost changes. Section 12 (Financial) depends on this structure.

## Rules:
- roles_and_responsibilities must have at least 1 entry with required_by_month, cost_range_annual, criticality
- hiring_timeline must have at least 1 entry showing when the first hire happens
- headcount_plan must cover at least month_0, month_6, month_12 (minimum 3 data points)
- personnel_policy must be at least 100 characters — state compensation philosophy, equity approach, FTE vs contractor
- knowledge_gaps must have at least 1 entry — no business has zero knowledge gaps
- Every role in hiring_timeline must appear in roles_and_responsibilities
- If revenue_assumptions show sales targets, hiring_timeline MUST include sales roles

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, roles_and_responsibilities, hiring_timeline, headcount_plan, personnel_policy, knowledge_gaps, hiring_risks, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
"""


class HRPlanAgent(BaseChildAgent):

    SYSTEM_PROMPT = SYSTEM_PROMPT
    AGENT_NAME = "HRPlan"
    AGENT_ROLE = (
        "Human Resources Plan — you design the hiring plan, sequence roles by priority, "
        "estimate costs, define personnel policy, and identify knowledge gaps"
    )
    SECTION_NUMBER = "11"
    MODEL_ENV = "CLAUDE_HAIKU_MODEL"
    MODEL_DEFAULT = "claude-haiku-4-5-20251001"
    INPUT_SCHEMA = HRPlanInput
    OUTPUT_SCHEMA = HRPlanOutput

    def _default_gap_key(self) -> str:
        return "business_model"

    def _extract_input(self, input_package: dict, task: dict) -> dict:
        return {
            "business_model": input_package.get("business_model", ""),
            "opportunity_description": input_package.get("opportunity_description", ""),
            "objectives": input_package.get("objectives", []),
            "team_gaps": input_package.get("team_gaps", []),
            "capability_gaps": input_package.get("capability_gaps", []),
            "org_structure": input_package.get("org_structure", ""),
            "strategic_implications": input_package.get("strategic_implications", ""),
            "priority_strategic_issues": input_package.get("priority_strategic_issues", []),
            "revenue_assumptions": input_package.get("revenue_assumptions", {}),
            "target_market_analysis": input_package.get("target_market_analysis", {}),
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

    def _build_ie_input_data(self, input_package: dict) -> dict:
        return {
            "business_model": input_package.get("business_model", ""),
            "opportunity_description": input_package.get("opportunity_description", ""),
            "objectives": input_package.get("objectives", []),
            "team_gaps": input_package.get("team_gaps", []),
            "capability_gaps": input_package.get("capability_gaps", []),
            "org_structure": input_package.get("org_structure", ""),
            "strategic_implications": input_package.get("strategic_implications", ""),
            "priority_strategic_issues": input_package.get("priority_strategic_issues", []),
            "revenue_assumptions": input_package.get("revenue_assumptions", {}),
            "target_market_analysis": input_package.get("target_market_analysis", {}),
            "acceptance_criteria": input_package.get("acceptance_criteria", ""),
        }

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON with these exact keys:
- section_number: "11"
- roles_and_responsibilities: list of {"role_title": str, "responsibilities": str (min 30 chars), "required_by_month": int (0-36), "cost_range_annual": str, "criticality": "critical"|"important"|"nice_to_have", "closes_gap": str|null}
- hiring_timeline: list of {"month": int (0-36), "role": str, "justification": str (min 20 chars), "prerequisite": str|null} (min 1 item)
- headcount_plan: dict with structure {"month_0": {"headcount": int, "total_cost_monthly": int, "roles": [str]}, "month_6": {...}, "month_12": {...}}
- personnel_policy: str (min 100 chars — compensation philosophy, equity approach, FTE vs contractor)
- knowledge_gaps: [str] (min 1 item)
- hiring_risks: [str]
- assumptions_used: list of {"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0"""

    def _build_prompt(self, inp: HRPlanInput) -> str:
        return f"""Design a human resources plan for this business.

BUSINESS MODEL: {inp.business_model}
OPPORTUNITY: {inp.opportunity_description}
OBJECTIVES: {json.dumps(inp.objectives, indent=2)}

TEAM GAPS (from Section 2): {json.dumps(inp.team_gaps, indent=2) if inp.team_gaps else "Not provided"}
CAPABILITY GAPS (from Section 4): {json.dumps(inp.capability_gaps, indent=2) if inp.capability_gaps else "Not provided"}

STRATEGIC IMPLICATIONS (from SWOT): {inp.strategic_implications or "Not provided"}
PRIORITY ISSUES: {json.dumps(inp.priority_strategic_issues, indent=2) if inp.priority_strategic_issues else "Not provided"}

REVENUE ASSUMPTIONS (from Marketing): {json.dumps(inp.revenue_assumptions, indent=2) if inp.revenue_assumptions else "Not provided"}

Return ONLY valid JSON with these exact keys:
- section_number: "11"
- roles_and_responsibilities: list of {{"role_title": str, "responsibilities": str (min 30 chars), "required_by_month": int (0-36), "cost_range_annual": str, "criticality": "critical"|"important"|"nice_to_have", "closes_gap": str|null}}
- hiring_timeline: list of {{"month": int (0-36), "role": str, "justification": str (min 20 chars), "prerequisite": str|null}} (min 1 item)
- headcount_plan: dict with structure {{"month_0": {{"headcount": int, "total_cost_monthly": int, "roles": [str]}}, "month_6": {{...}}, "month_12": {{...}}}}
- personnel_policy: str (min 100 chars)
- knowledge_gaps: [str] (min 1 item)
- hiring_risks: [str]
- assumptions_used: list of {{"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0
"""

    def _fallback_defaults(self, inp: HRPlanInput) -> dict:
        return {
            "section_number": "11",
            "roles_and_responsibilities": [
                {
                    "role_title": "VP Sales / Head of GTM",
                    "responsibilities": "Lead go-to-market strategy, build sales process, close first 10 customers",
                    "required_by_month": 3,
                    "cost_range_annual": "$120K-$150K + equity",
                    "criticality": "critical",
                    "closes_gap": "No GTM or sales experience in founding team",
                },
                {
                    "role_title": "Senior Engineer",
                    "responsibilities": "Build core product features, establish technical architecture, support founder",
                    "required_by_month": 6,
                    "cost_range_annual": "$100K-$130K + equity",
                    "criticality": "important",
                    "closes_gap": "Technical capacity to scale product development",
                },
            ],
            "hiring_timeline": [
                {
                    "month": 3,
                    "role": "VP Sales / Head of GTM",
                    "justification": "Critical for customer acquisition — founder cannot do both product and sales",
                    "prerequisite": None,
                },
                {
                    "month": 6,
                    "role": "Senior Engineer",
                    "justification": "Needed to scale product development as customer base grows",
                    "prerequisite": "Product-market fit signals",
                },
            ],
            "headcount_plan": {
                "month_0": {
                    "headcount": 1,
                    "total_cost_monthly": 8000,
                    "roles": ["Founder (CEO)"],
                },
                "month_3": {
                    "headcount": 2,
                    "total_cost_monthly": 20000,
                    "roles": ["Founder (CEO)", "VP Sales"],
                },
                "month_6": {
                    "headcount": 3,
                    "total_cost_monthly": 30000,
                    "roles": ["Founder (CEO)", "VP Sales", "Senior Engineer"],
                },
                "month_12": {
                    "headcount": 4,
                    "total_cost_monthly": 40000,
                    "roles": ["Founder (CEO)", "VP Sales", "Senior Engineer", "SDR/BDR"],
                },
            },
            "personnel_policy": (
                "Market-rate salaries for senior hires (VP-level) with 1-2% equity vesting over 4 years. "
                "IC-level hires at 90% market rate with 0.2-0.5% equity. Preference for full-time employees "
                "over contractors for core roles. Contractor budget reserved for specialized skills (design, legal, compliance)."
            ),
            "knowledge_gaps": [
                "Enterprise sales process and contract negotiation",
                "B2B SaaS pricing and packaging strategy",
                "Customer success and onboarding best practices",
            ],
            "hiring_risks": [
                "Competitive talent market for GTM roles in this geography",
                "Founder has no prior hiring experience — may mis-hire",
                "Equity pool may not be sufficient to attract senior talent if competitors offer better packages",
            ],
            "assumptions_used": [{
                "statement": "LLM output was unparseable — defaults used",
                "confidence": "low",
                "source": "assumed",
                "source_detail": None
            }],
            "uncertainties": [
                "LLM response could not be parsed — full analysis not completed",
                "Actual salary ranges depend on geography and candidate quality",
            ],
            "confidence_score": "low",
            "input_tokens": 0,
            "output_tokens": 0,
        }


async def main():
    await run_child_agent(HRPlanAgent, "HR_PLAN_JID", "HR_PLAN_PASSWORD")


if __name__ == "__main__":
    asyncio.run(main())
