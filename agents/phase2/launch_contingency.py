"""Launch & Contingency Agent — start-up programme, capital plan, contingency scenarios."""

import json
import logging

from agents.phase2.base_child_agent import BaseChildAgent
from agents.phase2.llm_utils import parse_json_with_retry
from agents.phase2.rag_mixin import rag_enrich
from schemas.inputs.launch_contingency import LaunchContingencyInput
from schemas.outputs.launch_contingency import LaunchContingencyOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Launch & Contingency agent in a multi-agent business plan system.
Your role: build the start-up programme with milestones that account for real dependencies, and
contingency plans that directly address the specific kill conditions identified in prior sections.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. DEPENDENCY-AWARE SEQUENCING (Milestones must respect reality)
   - Every milestone must list its dependencies explicitly. "Launch product" cannot come before "Complete MVP" which cannot come before "Hire engineer" (from Section 4 headcount_plan).
   - Cross-reference headcount_plan from Section 4: if a milestone requires a person who has not been hired yet, the milestone date must be AFTER their start date + ramp time (assume 3 months to productivity).
   - Cross-reference revenue_assumptions from Section 8: if "first customer" is a milestone, the timeline must account for the sales_cycle_months. A 6-month sales cycle means first customer cannot be Month 2.
   - If dependencies create a critical path longer than available runway, flag this as a FATAL conflict.

2. CONTINGENCY TIED TO KILL CONDITIONS (Not generic risk management)
   - Each contingency_scenario must address a specific risk identified in earlier sections:
     * primary_risk_factor from Section 12 (financial simulation)
     * High-severity threats from Section 5 (SWOT)
     * Capability gaps rated "high" from Section 4 (org design)
   - For each scenario, the "trigger" must be a measurable condition (e.g., "Revenue < $X by month Y" or "Churn > 15% for 2 consecutive months"), not a vague description.
   - The "response" must be a specific action with a timeline, not "pivot" or "reassess." What pivot? To what? In how long?

3. CAPITAL PLAN (Specific amounts tied to milestones)
   - Capital needs must trace to: burn_rate (from Section 12) x months_to_break_even + buffer (minimum 3 months).
   - State the specific funding round type (bootstrapped, pre-seed, seed, series A) and typical range for that type.
   - Tie funding milestones to business milestones: "Raise seed at $X after achieving [milestone Y] which de-risks [assumption Z]."
   - If the business plan shows no path to profitability within available capital, this must be stated bluntly.

4. EXIT CONDITIONS (Specific triggers, not feelings)
   - Exit conditions must be quantitative: "Wind down if [metric] < [threshold] by [date]."
   - Multiple triggers should be defined: cash runway trigger (< 3 months with no funding path), traction trigger (zero customers by month X), and market trigger (key market assumption disproved).
   - Exit conditions are not failures — they are rational decision points that protect the founder's time and remaining capital.

5. CRITICAL PATH IDENTIFICATION
   - The critical_path_item must be the single thing that, if delayed, delays EVERYTHING downstream.
   - It must connect to the longest dependency chain in the launch_programme.
   - State what happens if the critical path item is delayed by 3 months. What is the cascading impact?

## ANTI-PATTERNS — If you catch yourself writing any of these, you do not have enough information:
- NEVER write "build MVP" as a milestone without specifying scope, who builds it, and definition of done.
- NEVER write "achieve product-market fit" as a milestone — that is an outcome, not a milestone. What measurable signals indicate PMF? (e.g., "5 paying customers with <10% monthly churn")
- NEVER write "secure funding" without specifying: how much, from whom (type of investor), and what milestone makes you fundable.
- NEVER write a contingency plan that says "pivot to a different market" without specifying which market and why it is more viable.
- NEVER set milestones at even intervals (Month 3, Month 6, Month 9) unless the dependencies actually support that spacing.
- NEVER write exit_conditions as "if the business is not working" — define exactly what "not working" means in numbers.

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If break_even_analysis is empty AND probability_distribution is empty AND there is no primary_risk_factor, flag as FATAL: "Cannot build contingency plan without financial model outputs from Section 12."
- If the financial model shows >60% probability of cash-out AND there is no identified funding path, flag as FATAL: "Launch plan is not viable without addressing the >60% failure probability from simulation."

## OUTPUT LENGTH CONSTRAINTS — Obey these limits strictly:
- launch_programme: MAXIMUM 6 milestones. Pick the 6 most critical. Do NOT list every sub-task.
- prerequisite_conditions: MAXIMUM 4 items.
- contingency_scenarios: MAXIMUM 3 scenarios. Only the highest-impact scenarios.
- assumptions_used: MAXIMUM 5 assumptions. Only those that materially affect the plan.
- uncertainties: MAXIMUM 4 items.
- capital_plan: 2-3 sentences max.
- critical_path_item: 1-2 sentences max.
- exit_conditions: 2-3 sentences covering the key triggers.
- All string fields: MAXIMUM 200 characters each. Be concise.
- Total output must fit comfortably in one JSON response. Prefer brevity over exhaustiveness.

## Rules:
- launch_programme must have 3-6 milestones with target_date_months, responsible, success_metric, dependencies
- prerequisite_conditions must have 2-4 items — things that must be TRUE before launch starts
- capital_plan must be at least 50 characters explaining how and when to raise specific amounts
- critical_path_item must identify the single most important thing to get right first and why
- contingency_scenarios must be included when probability_distribution shows >30% cash-out rate
- exit_conditions must define when to wind down — specific quantitative triggers
- Milestones must be sequenced logically with explicit dependencies

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, launch_programme, prerequisite_conditions, capital_plan, critical_path_item, contingency_scenarios, exit_conditions, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
"""


class LaunchContingencyAgent(BaseChildAgent):
    SYSTEM_PROMPT = SYSTEM_PROMPT
    AGENT_NAME = "LaunchContingency"
    AGENT_ROLE = (
        "Launch & Contingency — you build the start-up programme with sequenced milestones, "
        "capital plan, critical path, contingency scenarios, and exit conditions"
    )
    SECTION_NUMBER = "13"
    MODEL_ENV = "CLAUDE_HAIKU_MODEL"
    MODEL_DEFAULT = "claude-haiku-4-5-20251001"
    INPUT_SCHEMA = LaunchContingencyInput
    OUTPUT_SCHEMA = LaunchContingencyOutput

    def _default_gap_key(self) -> str:
        return "budget_constraints"

    def reasoning_budget(self, revision_required: bool) -> int:
        return 3 if revision_required else 2

    def _rag_enrich(self) -> str:
        return rag_enrich(
            "launch plan contingency risks milestones compliance legal",
            section="13",
        )

    def _extract_input(self, input_package: dict, task: dict) -> dict:
        return {
            "revenue_assumptions": input_package.get("revenue_assumptions", {}),
            "headcount_plan": input_package.get("headcount_plan", {}),
            "break_even_analysis": input_package.get("break_even_analysis", {}),
            "probability_distribution": input_package.get("probability_distribution", []),
            "primary_risk_factor": input_package.get("primary_risk_factor", ""),
            "market_entry_strategy": input_package.get("market_entry_strategy", ""),
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

    def _build_ie_input_data(self, input_package: dict) -> dict:
        return {
            "revenue_assumptions": input_package.get("revenue_assumptions", {}),
            "headcount_plan": input_package.get("headcount_plan", {}),
            "break_even_analysis": input_package.get("break_even_analysis", {}),
            "probability_distribution": input_package.get("probability_distribution", []),
            "primary_risk_factor": input_package.get("primary_risk_factor", ""),
            "market_entry_strategy": input_package.get("market_entry_strategy", ""),
        }

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON with these exact keys:
- section_number: "13"
- launch_programme: list of {"milestone": str, "target_date_months": int, "responsible": str, "success_metric": str, "dependencies": [str]} (min 3, logically sequenced)
- prerequisite_conditions: [str] (min 2 — what must happen before launch)
- capital_plan: str (min 50 chars — how and when to raise capital, specific amounts)
- critical_path_item: str (the single most important thing to get right first)
- contingency_scenarios: list of {"scenario": str, "trigger": str, "response": str}|null
- exit_conditions: str|null (when to wind down — specific triggers, not vague)
- assumptions_used: list of {"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0"""

    def _build_prompt(self, validated_input: LaunchContingencyInput) -> str:
        return f"""Build the start-up programme and contingency plan.

REVENUE ASSUMPTIONS: {json.dumps(validated_input.revenue_assumptions, indent=2)}
HEADCOUNT PLAN: {json.dumps(validated_input.headcount_plan, indent=2)}
BREAK-EVEN ANALYSIS: {json.dumps(validated_input.break_even_analysis, indent=2)}
SIMULATION RESULTS (P10/P50/P90): {json.dumps(validated_input.probability_distribution, indent=2)}
PRIMARY RISK FACTOR: {validated_input.primary_risk_factor}
MARKET ENTRY STRATEGY: {validated_input.market_entry_strategy}

Return ONLY valid JSON with these exact keys:
- section_number: "13"
- launch_programme: list of {{"milestone": str, "target_date_months": int, "responsible": str, "success_metric": str, "dependencies": [str]}} (min 3)
- prerequisite_conditions: [str] (min 2, what must happen before launch)
- capital_plan: str (min 50 chars, how and when to raise capital)
- critical_path_item: str (the single most important thing to get right first)
- contingency_scenarios: list of {{"scenario": str, "trigger": str, "response": str}}|null
- exit_conditions: str|null (when to wind down if assumptions fail)
- assumptions_used: list of {{"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0
"""

    def _parse_llm_response(self, raw: str, validated_input: LaunchContingencyInput) -> dict:
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(validated_input),
            agent_name="LaunchContingency",
        )
        if result is not None:
            return result

        logger.warning("[LaunchContingency] Both parse attempts failed, using fallback")
        return self._fallback_defaults(validated_input)

    def _fallback_defaults(self, validated_input: LaunchContingencyInput) -> dict:
        return {
            "section_number": "13",
            "launch_programme": [
                {"milestone": "Complete MVP development", "target_date_months": 2, "responsible": "Founder", "success_metric": "Working prototype", "dependencies": []},
                {"milestone": "First customer acquisition", "target_date_months": 4, "responsible": "Founder", "success_metric": "Signed contract or first payment", "dependencies": ["Complete MVP development"]},
                {"milestone": "Achieve product-market fit signal", "target_date_months": 6, "responsible": "Team", "success_metric": "3+ paying customers with positive feedback", "dependencies": ["First customer acquisition"]},
            ],
            "prerequisite_conditions": ["Secure initial funding or bootstrap runway", "Validate core value proposition with target customers"],
            "capital_plan": "Bootstrap initial development with founder capital. Seek pre-seed funding at MVP stage to extend runway to 18 months.",
            "critical_path_item": "Validating that target customers will pay for the solution before investing in full build-out",
            "contingency_scenarios": [{"scenario": "No customers by month 6", "trigger": "Zero revenue at 6 months", "response": "Pivot positioning or target segment"}],
            "exit_conditions": "Wind down if cash runway drops below 3 months with no revenue traction",
            "assumptions_used": [{"statement": "LLM output was unparseable — defaults used", "confidence": "low", "source": "assumed", "source_detail": None}],
            "uncertainties": ["LLM response could not be parsed — full analysis not completed"],
            "confidence_score": "low",
            "input_tokens": 0,
            "output_tokens": 0,
        }
