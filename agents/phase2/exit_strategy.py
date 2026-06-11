import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging

from agents.phase2.base_child_agent import BaseChildAgent, run_child_agent
from schemas.inputs.exit_strategy import ExitStrategyInput
from schemas.outputs.exit_strategy import ExitStrategyOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Exit Strategy & Contingency Plan agent in a multi-agent business plan system.
Your role: design the exit path (acquisition/IPO), model cap table evolution, calculate investor returns, and define contingency triggers for pivot or wind-down scenarios.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. EXIT PATH REALISM (No fantasy IPOs for $5M ARR businesses)
   - **Acquisition (most common for startups)**:
     - Typical timeline: 3-7 years
     - Acquirer types: Strategic (buy for product/team/customers), Financial (PE/growth equity)
     - Valuation: 5-10x ARR for SaaS, 3-5x revenue for marketplaces, varies widely for deep tech
     - Name 3-5 plausible acquirers with rationale: "Company X would acquire us for [strategic fit / talent / IP / distribution]"
   - **IPO (rare for early-stage)**:
     - Minimum viable scale: $100M+ ARR, >40% YoY growth, strong unit economics
     - If revenue projections show <$20M ARR by Year 5, do NOT claim IPO as primary path
     - IPO timeline: 7-10+ years from founding
   - **Bootstrap to profitability (valid exit)**:
     - Retain 100% ownership, no institutional investors
     - Exit via dividend/distribution over time, or eventual sale at founder's discretion

2. CAP TABLE MATH (Dilution must add up)
   - **Standard dilution path**:
     - Pre-seed: Founders 100%
     - Seed ($500K-$2M at $5M-$10M post): Founders dilute 10-20%, investors 10-20%, ESOP 10-15%
     - Series A ($3M-$10M at $15M-$30M post): Founders dilute to 50-60%, investors 30-40%, ESOP 15-20%
     - Exit: Founders 40-50%, investors 40-50%, employees 10-15%
   - **Dilution formula**: New investment / post-money valuation = % diluted
   - If you raise $2M at $10M post, investors get 20%, existing holders dilute proportionally
   - NEVER show founders with 70% at exit after 3 funding rounds — the math doesn't work

3. INVESTOR RETURNS (Must justify why investors would invest)
   - **Seed investors** target: 10-20x return (high risk, early entry)
   - **Series A investors** target: 5-10x return
   - **Return calculation**: (exit valuation × ownership %) / investment amount
   - If exit valuation is $50M and seed investors own 15%, their return is: ($50M × 0.15) / $1M = 7.5x
   - Flag if returns are <3x for early investors — this is not venture-scale

4. CONTINGENCY TRIGGERS (Observable, actionable)
   - **Pivot triggers**: Specific milestones missed by specific dates
     - Good: "If CAC > $500 after 6 months with 50+ trials, pivot to lower-cost channel"
     - Bad: "If market conditions worsen, consider pivoting"
   - **Wind-down triggers**: Cash runway + revenue reality
     - Good: "If <$50K MRR by Month 18 and <6 months runway, initiate wind-down"
     - Bad: "If business isn't working, shut down"
   - **Tie to probability_distribution**: If SimPy P10 scenario shows cash-out before break-even, that's a contingency scenario

5. FAILURE SCENARIOS (from SimPy simulation)
   - Use probability_distribution from Section 12: P10 (pessimistic), P50 (baseline), P90 (optimistic)
   - P10 scenario typically shows: longer time to break-even, higher burn, cash-out risk
   - Contingency plan must address: What happens if we hit P10? (slow growth, extend runway, cut costs, pivot, wind down)

## ANTI-PATTERNS — If you catch yourself writing any of these, you do not have enough information:
- NEVER write "IPO in 5 years" for a pre-revenue startup with <$20M projected ARR
- NEVER write "strategic acquirer interest" without naming who and why they'd buy
- NEVER show founder ownership >60% at exit after raising 2+ rounds
- NEVER write "attractive returns for investors" without showing the math
- NEVER write "flexible exit options" — pick a primary path and justify it
- NEVER write "pivot if needed" — specify WHEN to pivot and to WHAT

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If business_type is empty AND year_3_revenue is empty, escalate with trigger "unclear_input" and gap_key "business_type".

## Rules:
- exit_strategy must include: primary_exit_path (acquisition/IPO/bootstrap), acquisition_targets (list 3-5 plausible acquirers with rationale), exit_timeline, exit_valuation (range based on comps)
- cap_table must show evolution: pre_seed, post_seed, post_series_a, exit_scenario with ownership %
- funding_strategy must show: seed_round (amount, timing, milestones), series_a, series_b (if applicable)
- investor_returns must show: seed_return_multiple, series_a_return_multiple, exit_valuation scenarios (P10/P50/P90)
- dilution_analysis must show: founder_dilution_path (month 0 → exit), employee_pool_sizing, investor_ownership
- exit_risks must have at least 2 items (market risks, acquisition landscape, valuation risks)
- contingency_scenarios (from dependency_map Section 14 outputs) integrated into exit_strategy
- exit_conditions (wind-down triggers) included

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, exit_strategy, cap_table, funding_strategy, investor_returns, dilution_analysis, exit_risks, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
"""


class ExitStrategyAgent(BaseChildAgent):

    SYSTEM_PROMPT = SYSTEM_PROMPT
    AGENT_NAME = "ExitStrategy"
    AGENT_ROLE = (
        "Exit Strategy & Contingency Plan — you design exit path, model cap table, "
        "calculate investor returns, and define contingency triggers for pivot/wind-down"
    )
    SECTION_NUMBER = "14"
    MODEL_ENV = "CLAUDE_SONNET_MODEL"
    MODEL_DEFAULT = "claude-sonnet-4-20250514"
    INPUT_SCHEMA = ExitStrategyInput
    OUTPUT_SCHEMA = ExitStrategyOutput

    def _default_gap_key(self) -> str:
        return "business_type"

    def _extract_input(self, input_package: dict, task: dict) -> dict:
        return {
            "business_type": input_package.get("business_type", ""),
            "market_size_tam": input_package.get("market_size_tam"),
            "year_3_revenue": input_package.get("year_3_revenue"),
            "year_3_arr": input_package.get("year_3_arr"),
            "break_even_year": input_package.get("break_even_year"),
            "profitability_year_3": input_package.get("profitability_year_3"),
            "target_market": input_package.get("target_market"),
            "competitive_positioning": input_package.get("competitive_positioning"),
            "industry_sector": input_package.get("industry_sector"),
            "geography": input_package.get("geography"),
            "founder_goals": input_package.get("founder_goals"),
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

    def _build_ie_input_data(self, input_package: dict) -> dict:
        return {
            "business_type": input_package.get("business_type", ""),
            "market_size_tam": input_package.get("market_size_tam"),
            "year_3_revenue": input_package.get("year_3_revenue"),
            "year_3_arr": input_package.get("year_3_arr"),
            "break_even_year": input_package.get("break_even_year"),
            "profitability_year_3": input_package.get("profitability_year_3"),
            "target_market": input_package.get("target_market"),
            "competitive_positioning": input_package.get("competitive_positioning"),
            "industry_sector": input_package.get("industry_sector"),
            "geography": input_package.get("geography"),
            "founder_goals": input_package.get("founder_goals"),
            "acceptance_criteria": input_package.get("acceptance_criteria", ""),
        }

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON with these exact keys:
- section_number: "14"
- exit_strategy: dict with "primary_exit_path", "acquisition_targets" (list 3-5 with rationale), "ipo_path" (if applicable), "exit_timeline", "exit_valuation" (range), "contingency_scenarios" (failure scenarios from SimPy P10), "exit_conditions" (wind-down triggers)
- cap_table: dict with "pre_seed", "post_seed", "post_series_a", "exit_scenario" (ownership % for founders/investors/employees at each stage)
- funding_strategy: dict with "seed_round" (amount, timing, milestones), "series_a" (amount, timing, milestones), "series_b" (if applicable)
- investor_returns: dict with "seed_return_multiple", "series_a_return_multiple", "exit_valuation" (P10/P50/P90 scenarios with investor returns)
- dilution_analysis: dict with "founder_dilution_path" (month_0 → exit), "employee_pool_sizing", "investor_ownership"
- exit_risks: [str] (min 2 items)
- assumptions_used: list of {"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0"""

    def _build_prompt(self, inp: ExitStrategyInput) -> str:
        return f"""Design an exit strategy and contingency plan for this business.

BUSINESS TYPE: {inp.business_type}
MARKET SIZE (TAM): {inp.market_size_tam or "Not provided"}
YEAR 3 REVENUE: {inp.year_3_revenue or "Not provided"}
YEAR 3 ARR: {inp.year_3_arr or "Not provided"}
BREAK-EVEN YEAR: {inp.break_even_year or "Not provided"}
PROFITABILITY YEAR 3: {inp.profitability_year_3 or "Not provided"}

TARGET MARKET: {inp.target_market or "Not provided"}
COMPETITIVE POSITIONING: {inp.competitive_positioning or "Not provided"}
INDUSTRY SECTOR: {inp.industry_sector or "Not provided"}
GEOGRAPHY: {inp.geography or "Not provided"}
FOUNDER GOALS: {inp.founder_goals or "Not provided"}

Return ONLY valid JSON with these exact keys:
- section_number: "14"
- exit_strategy: dict with "primary_exit_path", "acquisition_targets" (list with rationale), "ipo_path", "exit_timeline", "exit_valuation", "contingency_scenarios", "exit_conditions"
- cap_table: dict with "pre_seed", "post_seed", "post_series_a", "exit_scenario"
- funding_strategy: dict with "seed_round", "series_a", "series_b"
- investor_returns: dict with "seed_return_multiple", "series_a_return_multiple", "exit_valuation"
- dilution_analysis: dict with "founder_dilution_path", "employee_pool_sizing", "investor_ownership"
- exit_risks: [str] (min 2 items)
- assumptions_used: list of {{"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0
"""

    def _fallback_defaults(self, inp: ExitStrategyInput) -> dict:
        return {
            "section_number": "14",
            "exit_strategy": {
                "primary_exit_path": "acquisition",
                "acquisition_targets": [
                    "Strategic acquirer in academic software space seeking to expand research validation offerings",
                    "Larger SaaS platform targeting universities looking to add quality assurance capabilities",
                    "Education technology company with distribution seeking AI-powered research tools",
                ],
                "ipo_path": "Not viable — market size and projected scale below IPO threshold",
                "exit_timeline": "5-7 years from founding",
                "exit_valuation": "$30M-$60M (5-8x Year 5 ARR estimate)",
                "contingency_scenarios": [
                    "If CAC exceeds $800 by Month 12, pivot to lower-cost channel (content marketing, partnerships)",
                    "If <30 paying customers by Month 18, pivot to adjacent market or validate different ICP",
                    "If runway falls below 6 months with no revenue traction, initiate wind-down",
                ],
                "exit_conditions": "Wind down if: (1) <$300K ARR by end of Year 2 with no funding path, OR (2) 3 consecutive quarters of negative growth with <6 months runway",
            },
            "cap_table": {
                "pre_seed": {
                    "founders": "100%",
                    "investors": "0%",
                    "esop": "0%",
                },
                "post_seed": {
                    "founders": "75%",
                    "investors": "15%",
                    "esop": "10%",
                    "notes": "$1M seed at $5M post-money",
                },
                "post_series_a": {
                    "founders": "55%",
                    "investors": "30%",
                    "esop": "15%",
                    "notes": "$5M Series A at $20M post-money",
                },
                "exit_scenario": {
                    "founders": "45-50%",
                    "investors": "35-40%",
                    "employees": "10-15%",
                    "notes": "After Series B dilution",
                },
            },
            "funding_strategy": {
                "seed_round": {
                    "amount": "$1M-$1.5M",
                    "timing": "Month 6-9 (after initial customer traction)",
                    "milestones": "10 paying customers, $50K MRR, validated sales process",
                },
                "series_a": {
                    "amount": "$5M-$7M",
                    "timing": "Month 18-24 (after product-market fit)",
                    "milestones": "$500K ARR, proven unit economics, repeatable GTM motion",
                },
                "series_b": {
                    "amount": "$15M-$20M",
                    "timing": "Month 36-42 (scaling phase)",
                    "milestones": "$3M ARR, predictable growth, path to $10M ARR",
                },
            },
            "investor_returns": {
                "seed_return_multiple": "8-12x (assuming $50M exit)",
                "series_a_return_multiple": "5-7x (assuming $50M exit)",
                "exit_valuation": {
                    "P10": "$20M (3x seed investment, 2x Series A)",
                    "P50": "$50M (8x seed, 5x Series A)",
                    "P90": "$100M (15x seed, 10x Series A)",
                },
            },
            "dilution_analysis": {
                "founder_dilution_path": "Month 0: 100% → Post-seed: 75% → Post-A: 55% → Post-B: 45% → Exit: 45-50% (after ESOP dilution)",
                "employee_pool_sizing": "10% post-seed, expand to 15% post-A, 10-15% exercised by exit",
                "investor_ownership": "Seed: 15%, Series A: 15% additional (30% total), Series B: 10% additional (40% total)",
            },
            "exit_risks": [
                "Acquisition market risk: If academic software M&A activity declines, exit valuation multiples compress",
                "Valuation risk: Projected ARR may not support target exit valuation if growth slows",
                "Timing risk: Extended time to scale increases dilution and reduces founder ownership at exit",
            ],
            "assumptions_used": [{
                "statement": "LLM output was unparseable — defaults used",
                "confidence": "low",
                "source": "assumed",
                "source_detail": None
            }],
            "uncertainties": [
                "LLM response could not be parsed — full analysis not completed",
                "Exit valuation highly dependent on revenue scale and market conditions at exit",
                "Funding timing assumes milestone achievement on target schedule",
            ],
            "confidence_score": "low",
            "input_tokens": 0,
            "output_tokens": 0,
        }


async def main():
    await run_child_agent(ExitStrategyAgent, "EXIT_STRATEGY_JID", "EXIT_STRATEGY_PASSWORD")


if __name__ == "__main__":
    asyncio.run(main())
