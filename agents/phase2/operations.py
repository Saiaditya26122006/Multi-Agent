"""Operations Agent — defines production/delivery process, cost structure, capacity plan."""

import json
import logging
import os

from agents.phase2.base_child_agent import BaseChildAgent
from agents.phase2.llm_utils import parse_json_with_retry
from schemas.inputs.operations import OperationsInput
from schemas.outputs.operations import OperationsOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Operations agent in a multi-agent business plan system.
Your role: define how the business actually delivers value — production/delivery process, cost structure
that adds up, capacity plan with real bottleneck analysis, and tooling choices that are justified.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. COST ARITHMETIC (Must add up — no hand-waving)
   - fixed_costs + (variable_costs x volume_year1) + headcount_from_section_4 = total Year 1 operating cost.
   - This total must be cross-referenced against the revenue_assumptions from marketing (Section 8). If costs > revenue for 18+ months, flag the burn rate explicitly.
   - COGS per unit must be derivable: list each component cost that goes into delivering one unit to one customer.
   - Every cost item must have a source_label: "validated" (CEO confirmed), "benchmarked" (industry data), or "assumed" (your estimate). If more than 50% are "assumed," confidence_score must be "low."

2. TOOL AND TECHNOLOGY JUSTIFICATION
   - For every tool or platform recommended, answer: "Why this tool instead of the cheaper/free alternative?"
   - State the monthly/annual cost of each tool.
   - If the business is pre-revenue, default to free/open-source tools unless there is a specific capability gap that requires a paid tool.
   - Never recommend enterprise tools (Salesforce, SAP, etc.) for a team under 10 people unless there is a regulatory or integration requirement.

3. TIMELINE FEASIBILITY (Cross-check with Section 4 headcount)
   - Production milestones must be achievable with the headcount available at that point in time.
   - If the org design says "hire engineer at month 3" but the operations plan requires engineering output at month 1, flag the dependency conflict.
   - For each phase of the production_process, state: who does this work? When are they available? What is their capacity?

4. CAPACITY PLAN (Specific bottlenecks, not generic scaling)
   - At 2x volume: identify the FIRST thing that breaks. Is it a person's time? A tool's limit? A supplier's lead time? Server capacity?
   - At 5x volume: identify what requires fundamental architectural change (new hires, new systems, new suppliers) vs. what scales linearly.
   - State the cost of scaling at each threshold. "Hire more people" is not a capacity plan — "hire 2 additional engineers at $X each to handle Y additional load" is.

5. SUPPLIER AND DEPENDENCY RISK
   - If the business depends on a single supplier or platform (e.g., AWS, Stripe, a specific API), flag the concentration risk.
   - For each critical dependency: what happens if it goes down, raises prices 50%, or discontinues the product?

## ANTI-PATTERNS — If you catch yourself writing any of these, you do not have enough information:
- NEVER write "scalable infrastructure" without naming the specific infrastructure and its scaling mechanism.
- NEVER write "leverage automation to reduce costs" without naming what process is automated, what tool does it, and the cost reduction in dollars.
- NEVER write "agile development process" as an operations strategy — that is a methodology, not a delivery plan.
- NEVER list fixed costs without dollar amounts. "Office space" is not a cost — "$2,000/month for co-working space in [city]" is a cost.
- NEVER write "cloud-based solution" without naming the cloud provider and estimated monthly cost at current scale.

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If there are no revenue_assumptions AND no business_type provided, flag as FATAL: "Cannot design operations without knowing what the business delivers or at what scale."
- If the implied burn rate (fixed_costs + headcount) exceeds any reasonable funding assumption by 3x with no revenue in sight, flag as FATAL: "Operations cost structure is unsustainable without funding information."

## REQUIRED FIELDS — These MUST be present and populated in your output:
- confidence_score: MUST be "high", "medium", or "low" — NEVER omit this field
- production_process: MUST be present (min 50 chars)
- cost_structure: MUST include fixed_costs_monthly, cogs_per_unit, initial_cash, source, confidence

## OUTPUT LENGTH CONSTRAINTS — Obey these limits strictly:
- production_process: 50-400 characters. Describe steps concisely — no sub-headings or bullet prose.
- cost_structure: exactly 6 scalar fields (see schema below). Roll every fixed line item into the single fixed_costs_monthly total — do NOT emit per-item dicts.
- capacity_plan: MAXIMUM 300 characters. State the 2x and 5x bottleneck and solution in one sentence each.
- supplier_strategy: MAXIMUM 200 characters or null.
- rd_plan: MAXIMUM 200 characters or null.
- ip_analysis: MAXIMUM 200 characters or null.
- assumptions_used: MAXIMUM 6 items. Each: {statement (max 120 chars), confidence, source, source_detail}.
- uncertainties: MAXIMUM 4 items. Each MAXIMUM 120 characters.
- Total output must fit comfortably under 4000 tokens. Use numbers and short phrases, not paragraphs.

## Rules:
- production_process must be at least 50 characters describing step-by-step delivery
- cost_structure must include: fixed_costs_monthly, cogs_per_unit, variable_costs_per_unit, initial_cash, plus a single source and confidence label for the whole structure
- The cost_structure feeds the Section 12 financial model directly — it must be scalar numbers, not nested dicts
- If technology_description or ip_status are provided, include rd_plan and ip_analysis
- capacity_plan must address scalability — what specifically breaks at 2x and 5x volume
- If this is a pure services business, focus on delivery process rather than manufacturing

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields in this order: section_number, confidence_score, production_process, cost_structure, capacity_plan, supplier_strategy, rd_plan, ip_analysis, assumptions_used, uncertainties, input_tokens, output_tokens.
"""


class OperationsAgent(BaseChildAgent):
    SYSTEM_PROMPT = SYSTEM_PROMPT
    AGENT_NAME = "Operations"
    AGENT_ROLE = "Operations — you define the production/delivery process, cost structure, capacity plan, supplier strategy, and optionally R&D and IP analysis"
    SECTION_NUMBER = "10"
    MODEL_ENV = "CLAUDE_HAIKU_MODEL"
    MODEL_DEFAULT = "claude-haiku-4-5-20251001"
    INPUT_SCHEMA = OperationsInput
    OUTPUT_SCHEMA = OperationsOutput

    def _default_gap_key(self) -> str:
        return "cost_structure"

    def _extract_input(self, input_package: dict, task: dict) -> dict:
        return {
            "opportunity_description": input_package.get("opportunity_description", ""),
            "business_type": input_package.get("business_type", ""),
            "revenue_assumptions": input_package.get("revenue_assumptions", {}),
            "swot_matrix": input_package.get("swot_matrix", {}),
            "technology_description": input_package.get("technology_description"),
            "ip_status": input_package.get("ip_status"),
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

    def _build_ie_input_data(self, input_package: dict) -> dict:
        return {
            "opportunity_description": input_package.get("opportunity_description", ""),
            "business_type": input_package.get("business_type", ""),
            "revenue_assumptions": input_package.get("revenue_assumptions", {}),
            "swot_matrix": input_package.get("swot_matrix", {}),
            "technology_description": input_package.get("technology_description"),
            "ip_status": input_package.get("ip_status"),
        }

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON with these exact keys IN THIS ORDER:
- section_number: "10"
- confidence_score: "high"|"medium"|"low" (REQUIRED — never omit)
- production_process: str (50-400 chars — how product/service is delivered, concise steps)
- cost_structure: {"fixed_costs_monthly": float (>=0, total monthly fixed cost), "cogs_per_unit": float (>=0), "variable_costs_per_unit": float (>=0), "initial_cash": float (>0, starting capital required), "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "confidence": "high"|"medium"|"low"}
- capacity_plan: str (max 300 chars — what breaks at 2x and 5x volume)
- supplier_strategy: str (max 200 chars)|null
- rd_plan: str (max 200 chars)|null (only if tech innovation involved)
- ip_analysis: str (max 200 chars)|null (only if IP relevant)
- assumptions_used: list of {"statement": str (max 120 chars), "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null} (MAX 6 items)
- uncertainties: [str] (MAX 4 items, each max 120 chars)
- input_tokens: 0
- output_tokens: 0

CONSTRAINTS: Total output under 4000 tokens. Numbers and short phrases, not paragraphs."""

    def _build_prompt(self, validated_input) -> str:
        tech_section = ""
        if validated_input.technology_description:
            tech_section = f"\nTECHNOLOGY: {validated_input.technology_description}\nIP STATUS: {validated_input.ip_status or 'Not provided'}"

        return f"""Define the operations and production plan for this business.

OPPORTUNITY: {validated_input.opportunity_description}
BUSINESS TYPE: {validated_input.business_type}
REVENUE ASSUMPTIONS: {json.dumps(validated_input.revenue_assumptions, indent=2)}
SWOT: {json.dumps(validated_input.swot_matrix, indent=2)}{tech_section}

Return ONLY valid JSON with these exact keys IN THIS ORDER:
- section_number: "10"
- confidence_score: "high"|"medium"|"low" (REQUIRED — never omit)
- production_process: str (50-400 chars, concise delivery steps)
- cost_structure: {{"fixed_costs_monthly": float (>=0, total monthly fixed cost), "cogs_per_unit": float (>=0), "variable_costs_per_unit": float (>=0), "initial_cash": float (>0), "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "confidence": "high"|"medium"|"low"}}
- capacity_plan: str (max 300 chars — 2x and 5x bottlenecks)
- supplier_strategy: str (max 200 chars)|null
- rd_plan: str (max 200 chars)|null
- ip_analysis: str (max 200 chars)|null
- assumptions_used: list of {{"statement": str (max 120 chars), "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}} (MAX 6 items)
- uncertainties: [str] (MAX 4 items, each max 120 chars)
- input_tokens: 0
- output_tokens: 0

CONSTRAINTS: Total output under 4000 tokens. Numbers and short phrases, not paragraphs.
"""

    def _parse_llm_response(self, raw: str, validated_input) -> dict:
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(validated_input),
            agent_name="Operations",
        )
        if result is not None:
            return result

        logger.warning("[Operations] Both parse attempts failed, using fallback")
        return self._fallback_defaults(validated_input)

    def _fallback_defaults(self, validated_input) -> dict:
        return {
            "section_number": "10",
            "production_process": f"Service delivery process for {validated_input.business_type or 'this venture'} involving customer acquisition, onboarding, and ongoing delivery",
            "cost_structure": {
                "fixed_costs_monthly": 2000.0,
                "cogs_per_unit": 20.0,
                "variable_costs_per_unit": 0.0,
                "initial_cash": 100000.0,
                "source": "assumed",
                "confidence": "low",
            },
            "capacity_plan": "Initial capacity supports Year 1 volume. At 2x volume, hire additional staff. At 5x, invest in automation.",
            "supplier_strategy": None,
            "rd_plan": None,
            "ip_analysis": None,
            "assumptions_used": [{
                "statement": "LLM output was unparseable — defaults used",
                "confidence": "low",
                "source": "assumed",
                "source_detail": None
            }],
            "uncertainties": ["LLM response could not be parsed — full analysis not completed"],
            "confidence_score": "low",
            "input_tokens": 0,
            "output_tokens": 0,
        }
