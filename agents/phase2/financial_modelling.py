import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging
import os
from typing import Optional

import boto3
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message

from memory.redis_client import RedisClient
from agents.phase2.llm_utils import parse_json_with_retry, signal_ready
from agents.phase2.intelligence_engine import IntelligenceEngine
from schemas.inputs.financial_modelling import FinancialModellingInput
from schemas.outputs.financial_modelling import FinancialModellingOutput
from simulation.financial_sim import run_simulation

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills" / "financial"

SYSTEM_PROMPT = """You are the Financial Modelling agent in a multi-agent business plan system.
Your role: build a complete financial plan including a 3-statement model, break-even analysis,
DCF valuation (conditional), and comparable company analysis.

You will be provided with financial skill documents that define methodology.
Follow them precisely for model construction.

Rules:
- three_statement_model must include: pl_monthly_year1, pl_annual_years2_3, balance_sheet, cash_flow
- break_even_analysis must include: baseline_month, optimistic_month, pessimistic_month, units_required
- Every assumption must be logged with label (validated/alex_provided/agent_inferred/assumed)
- risk_mitigation_actions must have at least 2 items
- DCF valuation is optional — only include if revenue assumptions have evidence
- SimPy simulation results will be provided separately — integrate them into your output
- All financial figures must be internally consistent across statements

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, three_statement_model, break_even_analysis, risk_mitigation_actions, dcf_valuation, comps_table, assumption_log, uncertainties, confidence_score, input_tokens, output_tokens.
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
        self.redis.client.set(f"task_output:{task_id}", json.dumps(result, default=str), ex=3600)

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
        return """Return ONLY valid JSON with these exact keys:
- section_number: "12"
- three_statement_model: {"pl_monthly_year1": [...], "pl_annual_years2_3": [...], "balance_sheet": {...}, "cash_flow": {...}}
- break_even_analysis: {"baseline_month": int, "optimistic_month": int, "pessimistic_month": int, "units_required": int}
- risk_mitigation_actions: [str] (min 2 items — specific actions, not platitudes)
- dcf_valuation: dict|null (include only if revenue assumptions have evidence beyond "assumed")
- comps_table: dict|null
- assumption_log: list of {"name": str, "value": str, "label": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source": str|null}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0

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

Return ONLY valid JSON with these exact keys:
- section_number: "12"
- three_statement_model: {{"pl_monthly_year1": [...], "pl_annual_years2_3": [...], "balance_sheet": {{...}}, "cash_flow": {{...}}}}
- break_even_analysis: {{"baseline_month": int, "optimistic_month": int, "pessimistic_month": int, "units_required": int}}
- risk_mitigation_actions: [str] (min 2 items)
- dcf_valuation: dict|null (include only if revenue assumptions are not purely assumed)
- comps_table: dict|null
- assumption_log: list of {{"name": str, "value": str, "label": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source": str|null}}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0

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
