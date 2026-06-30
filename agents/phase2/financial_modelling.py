import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

import boto3
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour

from agents.phase2.rag_mixin import rag_enrich, rag_check_killed
from spade.message import Message

from memory.redis_client import RedisClient
from agents.phase2.llm_utils import parse_json_with_retry, signal_ready
from agents.phase2.intelligence_engine import IntelligenceEngine
from schemas.inputs.financial_modelling import FinancialModellingInput
from schemas.outputs.financial_modelling import FinancialModellingOutput
from simulation.financial_sim import run_simulation
from services.search_service import search_for_section

logger = logging.getLogger(__name__)

SEARCH_QUERIES = {
    "12": [
        "B2B SaaS gross margin benchmarks institutional 2025",
        "academic software CAC payback period benchmarks",
        "university SaaS contract value annual recurring revenue"
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

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills" / "financial"

SYSTEM_PROMPT = """You are the Financial Modelling agent in a multi-agent business plan system.
Your role: build a financial model where every number traces to an upstream assumption, growth rates
are justified by market evidence, and break-even sensitivity is explicit about what kills the business.

You will be provided with financial skill documents that define methodology.
Follow them precisely for model construction.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. UPSTREAM TRACEABILITY (Every number has a parent)
   - Revenue line: price_per_unit (from Section 8 marketing) x volume (from Section 8) = monthly revenue. Show this chain.
   - Cost line: fixed_costs (from Section 10 operations) + variable_costs x volume + headcount (from Section 4 org design) = monthly costs.
   - If any input number is missing or labelled "assumed" upstream, your financial projections inherit that uncertainty — state it.
   - If revenue_assumptions and cost_structure are both empty/defaults, the entire financial model is speculative. Set confidence_score to "low" and state this prominently.

2. GROWTH RATE JUSTIFICATION (No hockey sticks without evidence)
   - Year-over-year growth must be justified by at least one of: market size headroom, conversion rate improvement, new channel addition, or price increase.
   - If volume_year2 > 3x volume_year1, you must explain what specifically changes to drive that growth. "Growing market" is not sufficient — what sales motion, channel, or product change enables 3x?
   - Compare your implied growth rate against industry benchmarks. If your model assumes faster growth than the industry median, flag it as "aggressive" in assumption_log.
   - Median SaaS growth Year 1-2 is ~80-100% (T2D3 pattern). If your model exceeds this without a network effect or viral mechanism, justify why.

3. BREAK-EVEN SENSITIVITY (What assumptions must hold?)
   - Identify the top 3 assumptions that most affect break-even month. For each: "If [assumption] is wrong by 30%, break-even shifts from month X to month Y."
   - The difference between optimistic_month and pessimistic_month must be explainable: what specific assumptions change between scenarios?
   - units_required for break-even must trace from: fixed_costs / (price_per_unit - variable_cost_per_unit). Show this math.

4. CASH FLOW REALITY CHECK
   - Cash flow is not the same as P&L. Account for: payment terms (customers may pay 30-90 days late), upfront costs (hiring, equipment), and working capital needs.
   - If the model shows negative cash flow for >12 months, calculate total funding required to survive. Flag if this exceeds typical seed/pre-seed ranges ($500K-$2M).
   - Monthly burn rate = fixed_costs + headcount_costs + (variable_costs x current volume) - revenue. State this number clearly for Month 1, Month 6, Month 12.

5. SIMULATION INTEGRATION
   - SimPy results provide probability distributions. Use them to calibrate optimistic/pessimistic scenarios.
   - If simulation shows >30% probability of cash-out before break-even, this is a critical risk — it must appear in risk_mitigation_actions.
   - primary_risk_factor from simulation must be directly addressed in risk_mitigation_actions.

6. INTERNAL CONSISTENCY CHECK
   - P&L revenue must match: price x volume for each period.
   - P&L costs must match: fixed_costs + (variable x volume) + headcount for each period.
   - Cash flow must account for working capital changes (receivables, payables).
   - Balance sheet assets - liabilities must equal equity. If they do not balance, you have an error.

## ANTI-PATTERNS — If you catch yourself writing any of these, you do not have enough information:
- NEVER write "conservative estimates" without showing what the aggressive estimate would be and why you chose the lower number.
- NEVER show Year 3 revenue > $10M for a bootstrapped startup without explaining the specific scaling mechanism.
- NEVER write "revenue grows 100% year over year" without naming the driver of that growth.
- NEVER omit CAC from the P&L. Customer acquisition is a real cost — it must appear in sales/marketing expense.
- NEVER write "break-even in Month 12" without showing the math: what monthly revenue level and what cost level makes that true.
- NEVER write "diversified revenue streams" in a Year 1 model. Pre-PMF startups should have ONE revenue stream.

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If revenue_assumptions are entirely empty AND cost_structure is entirely empty, flag as FATAL: "Cannot build financial model without revenue or cost inputs from Sections 8 and 10."
- If the model shows negative unit economics (price_per_unit < variable_cost_per_unit) with no path to improvement, flag as FATAL: "Business loses money on every unit sold — fundamental model is broken."
- If break-even requires >5 years AND there is no DCF justification for patient capital, flag as FATAL: "Break-even timeline exceeds viable runway for this stage."

## REQUIRED FIELDS — These MUST be present and populated in your output:
- confidence_score: MUST be "high", "medium", or "low" — NEVER omit this field
- risk_mitigation_actions: MUST have 2-5 items that directly address simulation risks
- break_even_analysis: MUST include baseline_month, optimistic_month, pessimistic_month, units_required

## OUTPUT LENGTH CONSTRAINTS — Obey these limits strictly:
- pl_monthly_year1: MAXIMUM 12 rows. Each row: {month, revenue, costs, net} — 4 fields only. No sub-breakdowns.
- pl_annual_years2_3: MAXIMUM 2 rows (year 2, year 3). Each: {year, revenue, costs, net, headcount_cost}.
- balance_sheet: MAXIMUM 6 key-value pairs (assets, liabilities, equity, cash, receivables, payables).
- cash_flow: MAXIMUM 4 key-value pairs (operating, investing, financing, net_change).
- break_even_analysis: Exactly 4 fields: baseline_month, optimistic_month, pessimistic_month, units_required.
- risk_mitigation_actions: 2-5 items. Each item MAXIMUM 150 characters.
- dcf_valuation: If included, MAXIMUM 5 key-value pairs. Omit (null) if assumptions are "assumed".
- comps_table: If included, MAXIMUM 3 comparable companies with 4 fields each. Omit (null) if not applicable.
- assumption_log: MAXIMUM 8 assumptions. Only the most material ones.
- uncertainties: MAXIMUM 5 items. Each MAXIMUM 150 characters.
- All string values: MAXIMUM 200 characters. Be concise — numbers over prose.
- Total output must fit comfortably under 4000 tokens. Prefer numbers and short phrases over paragraphs.

## Rules:
- three_statement_model must include: pl_monthly_year1, pl_annual_years2_3, balance_sheet, cash_flow
- break_even_analysis must include: baseline_month, optimistic_month, pessimistic_month, units_required
- Every assumption must be logged with label (validated/alex_provided/agent_inferred/assumed)
- DCF valuation is optional — only include if revenue assumptions have evidence beyond "assumed"
- SimPy simulation results will be provided separately — integrate them into your output
- All financial figures must be internally consistent across statements

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields in this order: section_number, confidence_score, risk_mitigation_actions, break_even_analysis, three_statement_model, dcf_valuation, comps_table, assumption_log, uncertainties, input_tokens, output_tokens.
"""


def _load_skill(skill_name: str) -> str:
    """Load a financial skill markdown file."""
    path = SKILLS_DIR / f"{skill_name}.md"
    if path.exists():
        return path.read_text()
    return ""


class ListenBehaviour(CyclicBehaviour):

    async def run(self):
        msg = await self.receive(timeout=5)
        if msg is None:
            return

        performative = msg.get_metadata("performative")
        task_id = msg.get_metadata("task_id")
        session_id = msg.get_metadata("session_id")
        pipeline_run_id = msg.get_metadata("pipeline_run_id")
        content = json.loads(msg.body)
        sender = str(msg.sender)

        if performative == "request":
            await self.agent.handle_request(task_id, session_id, pipeline_run_id, content)
        elif performative == "revise":
            await self.agent.handle_revise(task_id, session_id, pipeline_run_id, content)
        elif performative == "propose":
            await self.agent.handle_propose(task_id, session_id, pipeline_run_id, sender, content)


class FinancialModellingAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"))
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)
        self.simpy_runs = int(os.getenv("SIMPY_SIMULATION_RUNS", "1000"))

    async def setup(self):
        logger.info("[FinancialModelling] Starting")
        self.add_behaviour(ListenBehaviour())
        signal_ready(self.redis, "financial_modelling")

    async def _send_msg(self, msg: Message):
        class _Send(OneShotBehaviour):
            async def run(self_b):
                await self_b.send(msg)
        b = _Send()
        self.add_behaviour(b)
        await b.join(timeout=10)

    async def handle_request(self, task_id, session_id, pipeline_run_id, content):
        task = content.get("task", {})
        input_package = task.get("input_package", {})

        try:
            validated_input = FinancialModellingInput(
                task_id=task_id,
                session_id=session_id,
                revenue_assumptions=input_package.get("revenue_assumptions", {}),
                cac_assumptions=input_package.get("cac_assumptions", {}),
                cost_structure=input_package.get("cost_structure", {}),
                headcount_plan=input_package.get("headcount_plan", {}),
                business_type=input_package.get("business_type", ""),
                opportunity_description=input_package.get("opportunity_description", ""),
                market_context=input_package.get("market_context", ""),
                simpy_runs=self.simpy_runs,
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[FinancialModelling] Input validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "unclear_input", str(e))
            return

        rag_context = rag_enrich(
            "pricing decisions revenue costs funding WTP financial projections",
            section="12",
        )
        if rag_context:
            input_package["rag_financial_context"] = rag_context

        sim_assumptions = self._build_sim_assumptions(validated_input)
        logger.info("[FinancialModelling] Running SimPy with %d runs", self.simpy_runs)
        sim_results = run_simulation(sim_assumptions, num_runs=self.simpy_runs)
        logger.info("[FinancialModelling] SimPy complete — primary risk: %s", sim_results["primary_risk_factor"])

        skills_loaded = []
        skills_content = ""
        for skill_name in validated_input.financial_skills:
            skill_text = _load_skill(skill_name)
            if skill_text:
                skills_content += f"\n\n--- SKILL: {skill_name} ---\n{skill_text}"
                skills_loaded.append(skill_name)

        cross_context = input_package.get("cross_section_context", {})
        learning_context = input_package.get("learning_context", "")

        revision_required = input_package.get("revision_required", False)
        revision_feedback = input_package.get("revision_feedback", "")
        if revision_required and revision_feedback:
            learning_context += f"\n\nMANDATORY REVISIONS (from quality review):\n{revision_feedback}\nFix these issues. Do NOT weaken your analysis — make it more rigorous."

        input_data = {
            "revenue_assumptions": input_package.get("revenue_assumptions", {}),
            "cac_assumptions": input_package.get("cac_assumptions", {}),
            "cost_structure": input_package.get("cost_structure", {}),
            "headcount_plan": input_package.get("headcount_plan", {}),
            "business_type": input_package.get("business_type", ""),
            "opportunity_description": input_package.get("opportunity_description", ""),
            "market_context": input_package.get("market_context", ""),
            "simpy_simulation_results": sim_results,
            "financial_skills": skills_content[:3000] if skills_content else "No skill files loaded",
            "live_market_data": _get_live_market_data("12"),
        }

        parsed, reasoning_trace, token_usage = await self.intelligence.reason_and_produce(
            agent_role=(
                "Financial Modelling — you build 3-statement models, break-even analysis, "
                "DCF valuation, and integrate Monte Carlo simulation results into the financial plan"
            ),
            input_data=input_data,
            output_schema_prompt=self._build_schema_prompt(),
            cross_section_context=cross_context if cross_context else None,
            reasoning_budget=4,
            learning_context=learning_context,
        )

        if not parsed:
            user_message = self._build_prompt(validated_input, sim_results, skills_content)
            llm_response, fallback_usage = await self._call_llm(user_message)
            if not llm_response:
                await self._escalate(task_id, session_id, pipeline_run_id, "weak_evidence", "Intelligence engine and fallback both failed")
                return
            parsed = self._parse_llm_response(llm_response, validated_input)
            token_usage["input_tokens"] = token_usage.get("input_tokens", 0) + fallback_usage.get("input_tokens", 0)
            token_usage["output_tokens"] = token_usage.get("output_tokens", 0) + fallback_usage.get("output_tokens", 0)

        try:
            parsed["task_id"] = task_id
            parsed["model_used"] = self.model_id
            parsed["input_tokens"] = token_usage.get("input_tokens", 0)
            parsed["output_tokens"] = token_usage.get("output_tokens", 0)
            parsed["simpy_runs_completed"] = sim_results["runs_completed"]
            parsed["financial_skills_applied"] = skills_loaded
            parsed["probability_distribution"] = sim_results["probability_distribution"]
            parsed["primary_risk_factor"] = sim_results["primary_risk_factor"]
            validated_output = FinancialModellingOutput(**parsed)
        except Exception as e:
            logger.error("[FinancialModelling] Output validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "output_conflict", str(e))
            return

        result = validated_output.model_dump()
        result["reasoning_trace"] = reasoning_trace

        await self._check_contradictions(task_id, session_id, pipeline_run_id, validated_input, sim_results, result)
        await self._send_inform(task_id, session_id, pipeline_run_id, result)

    async def _check_contradictions(self, task_id, session_id, pipeline_run_id, inp, sim_results, output):
        """Detect input contradictions and fire propose if found."""
        rev = inp.revenue_assumptions
        price = rev.get("price_per_unit", 0)
        vol1 = rev.get("volume_year1", 0)
        marketing_year1_revenue = price * vol1

        break_even = output.get("break_even_analysis", {})
        baseline_month = break_even.get("baseline_month", 0)

        # If simulation says break-even >30 months but marketing implies profitability in year 1
        if baseline_month > 30 and marketing_year1_revenue > 0:
            ratio = baseline_month / 12
            if ratio > 2.5:
                mother_jid = os.getenv("MOTHER_AGENT_JID", "")
                msg = Message(to=mother_jid)
                msg.set_metadata("performative", "propose")
                msg.set_metadata("task_id", task_id)
                msg.set_metadata("session_id", session_id)
                msg.set_metadata("pipeline_run_id", pipeline_run_id)
                msg.body = json.dumps({
                    "target_agent": "marketing_strategy",
                    "proposal": f"Revenue assumptions yield break-even at month {baseline_month}, "
                                f"which conflicts with marketing's volume projections. "
                                f"Suggest revising volume_year1 upward or reducing CAC estimate.",
                    "field": "revenue_assumptions",
                    "evidence": {"break_even_month": baseline_month, "marketing_year1_revenue": marketing_year1_revenue},
                })
                await self._send_msg(msg)
                logger.info("[FinancialModelling] Proposed revenue adjustment to marketing agent")

    async def handle_revise(self, task_id, session_id, pipeline_run_id, content):
        """Handle revision request from Council Agent."""
        revision_instructions = content.get("revision_instructions", "")
        original_output = content.get("original_output", {})
        persona_critiques = content.get("persona_critiques", [])

        critique_text = "\n".join(
            f"- [{c.get('persona', '')}] {c.get('top_finding', '')}"
            for c in persona_critiques if c.get("severity") in ("critical", "minor")
        )

        input_package = {
            "revenue_assumptions": original_output.get("revenue_assumptions", {}),
            "cac_assumptions": original_output.get("cac_assumptions", {}),
            "cost_structure": original_output.get("cost_structure", {}),
            "headcount_plan": original_output.get("headcount_plan", {}),
            "revision_required": True,
            "revision_feedback": f"COUNCIL REVIEW FEEDBACK:\n{revision_instructions}\n\nSPECIFIC CRITIQUES:\n{critique_text}",
            "cross_section_context": content.get("cross_section_context", {}),
        }

        revised_content = {"task": {"input_package": input_package, "task_id": task_id}}
        await self.handle_request(task_id, session_id, pipeline_run_id, revised_content)

    async def handle_propose(self, task_id, session_id, pipeline_run_id, sender, content):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"status": "accepted", "proposal": content.get("proposal", "")})
        await self._send_msg(msg)

    def _build_sim_assumptions(self, inp: FinancialModellingInput) -> dict:
        rev = inp.revenue_assumptions
        cac = inp.cac_assumptions
        costs = inp.cost_structure
        hc = inp.headcount_plan

        year1_hc_cost = hc.get("year_1", {}).get("cost", 240000)

        return {
            "price_per_unit": rev.get("price_per_unit", 100),
            "volume_year1": rev.get("volume_year1", 100),
            "volume_year2": rev.get("volume_year2", 500),
            "volume_year3": rev.get("volume_year3", 1500),
            "sales_cycle_months": rev.get("sales_cycle_months", 3),
            "churn_rate": rev.get("churn_rate", 0.12),
            "conversion_rate": rev.get("conversion_rate", 0.01),
            "cac": cac.get("cac_estimate", 500),
            "fixed_costs_monthly": costs.get("fixed_costs_monthly", 10000),
            "variable_cost_per_unit": costs.get("cogs_per_unit", 0),
            "headcount_cost_monthly": year1_hc_cost / 12,
            "initial_cash": costs.get("initial_cash", 100000),
            "leads_per_month": rev.get("leads_per_month", 1000),
        }

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON with these exact keys IN THIS ORDER:
- section_number: "12"
- confidence_score: "high"|"medium"|"low" (REQUIRED — never omit)
- risk_mitigation_actions: [str] (2-5 items, each max 150 chars — REQUIRED)
- break_even_analysis: {"baseline_month": int, "optimistic_month": int, "pessimistic_month": int, "units_required": int}
- three_statement_model: {"pl_monthly_year1": [max 12 rows, each {month, revenue, costs, net}], "pl_annual_years2_3": [2 rows], "balance_sheet": {max 6 fields}, "cash_flow": {max 4 fields}}
- dcf_valuation: dict|null (max 5 fields, omit if assumptions are "assumed")
- comps_table: dict|null (max 3 companies, 4 fields each)
- assumption_log: list of {"name": str, "value": str, "label": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source": str|null} (MAX 8 items)
- uncertainties: [str] (MAX 5 items, each max 150 chars)
- input_tokens: 0
- output_tokens: 0

CONSTRAINTS: Total output must be under 4000 tokens. Use numbers and short phrases, not paragraphs. Each P&L row is 4 fields only.

NOTE: probability_distribution, primary_risk_factor, simpy_runs_completed, financial_skills_applied, model_used, and task_id will be added automatically. Do NOT include them."""

    def _build_prompt(self, inp: FinancialModellingInput, sim_results: dict, skills_content: str) -> str:
        return f"""Build a complete financial model for this business.

REVENUE ASSUMPTIONS: {json.dumps(inp.revenue_assumptions, indent=2)}
CAC ASSUMPTIONS: {json.dumps(inp.cac_assumptions, indent=2)}
COST STRUCTURE: {json.dumps(inp.cost_structure, indent=2)}
HEADCOUNT PLAN: {json.dumps(inp.headcount_plan, indent=2)}
BUSINESS TYPE: {inp.business_type}
OPPORTUNITY: {inp.opportunity_description}
MARKET CONTEXT: {inp.market_context}

SIMPY SIMULATION RESULTS (1000 runs):
{json.dumps(sim_results, indent=2)}

FINANCIAL SKILLS (follow these methodologies):
{skills_content}

Return ONLY valid JSON with these exact keys IN THIS ORDER:
- section_number: "12"
- confidence_score: "high"|"medium"|"low" (REQUIRED — never omit)
- risk_mitigation_actions: [str] (2-5 items, each max 150 chars — REQUIRED)
- break_even_analysis: {{"baseline_month": int, "optimistic_month": int, "pessimistic_month": int, "units_required": int}}
- three_statement_model: {{"pl_monthly_year1": [max 12 rows, each {{month, revenue, costs, net}}], "pl_annual_years2_3": [2 rows], "balance_sheet": {{max 6 fields}}, "cash_flow": {{max 4 fields}}}}
- dcf_valuation: dict|null (max 5 fields, omit if assumptions are "assumed")
- comps_table: dict|null (max 3 companies, 4 fields each)
- assumption_log: list of {{"name": str, "value": str, "label": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source": str|null}} (MAX 8 items)
- uncertainties: [str] (MAX 5 items, each max 150 chars)
- input_tokens: 0
- output_tokens: 0

CONSTRAINTS: Total output must be under 4000 tokens. Use numbers and short phrases, not paragraphs. Each P&L row is 4 fields only.

NOTE: probability_distribution, primary_risk_factor, simpy_runs_completed, financial_skills_applied, model_used, and task_id will be added automatically. Do NOT include them.
"""

    def _parse_llm_response(self, raw: str, inp: FinancialModellingInput) -> dict:
        """Parse LLM response with retry before falling back to defaults."""
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(inp, {}, ""),
            agent_name="FinancialModelling",
            max_tokens=8192,
        )
        if result is not None:
            return result

        logger.warning("[FinancialModelling] Both parse attempts failed, constructing fallback")
        rev = inp.revenue_assumptions
        price = rev.get("price_per_unit", 100)
        vol1 = rev.get("volume_year1", 100)
        return {
            "section_number": "12",
            "three_statement_model": {
                "pl_monthly_year1": [{"month": i, "revenue": price * (vol1 / 12), "costs": 10000, "net": price * (vol1 / 12) - 10000} for i in range(1, 13)],
                "pl_annual_years2_3": [{"year": 2, "revenue": price * rev.get("volume_year2", 500)}, {"year": 3, "revenue": price * rev.get("volume_year3", 1500)}],
                "balance_sheet": {"assets": 100000, "liabilities": 0, "equity": 100000},
                "cash_flow": {"operating": 0, "investing": -50000, "financing": 100000},
            },
            "break_even_analysis": {"baseline_month": 18, "optimistic_month": 12, "pessimistic_month": 24, "units_required": int(120000 / max(price - 20, 1))},
            "risk_mitigation_actions": ["Maintain 6-month cash runway at all times", "Implement monthly financial review cadence"],
            "dcf_valuation": None,
            "comps_table": None,
            "assumption_log": [{"name": "LLM parse failure", "value": "Fallback defaults used", "label": "assumed", "source": None}],
            "uncertainties": ["LLM response could not be parsed — full financial model not completed"],
            "confidence_score": "low",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    async def _call_llm(self, user_message: str) -> tuple[Optional[str], dict]:
        try:
            response = self.bedrock.converse(
                modelId=self.model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": user_message}]}],
                inferenceConfig={"maxTokens": 8192},
            )
            usage = response.get("usage", {})
            text = response["output"]["message"]["content"][0]["text"]
            return text, {"input_tokens": usage.get("inputTokens", 0), "output_tokens": usage.get("outputTokens", 0)}
        except Exception as e:
            logger.error("[FinancialModelling] LLM call failed: %s", e)
            return None, {}

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "12", "gap_key": "cost_structure"})
        await self._send_msg(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        council_jid = os.getenv("COUNCIL_AGENT_JID", "")
        target_jid = council_jid if council_jid else os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=target_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({
            "output": output,
            "section_number": "12",
            "agent_name": "financial_modelling",
        })
        await self._send_msg(msg)


async def main():
    from dotenv import load_dotenv
    load_dotenv()
    jid = os.getenv("FINANCIAL_MODELLING_JID")
    password = os.getenv("FINANCIAL_MODELLING_PASSWORD")
    if not jid or not password:
        raise ValueError("FINANCIAL_MODELLING_JID and PASSWORD must be set")
    agent = FinancialModellingAgent(jid=jid, password=password)
    await agent.start(auto_register=True)
    try:
        while agent.is_alive():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
