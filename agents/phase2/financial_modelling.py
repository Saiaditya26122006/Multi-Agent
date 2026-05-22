import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

import boto3
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour
from spade.message import Message

from memory.redis_client import RedisClient
from schemas.inputs.financial_modelling import FinancialModellingInput
from schemas.outputs.financial_modelling import FinancialModellingOutput
from simulation.financial_sim import run_simulation

logger = logging.getLogger(__name__)

SKILLS_DIR = Path("skills/financial")

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
        elif performative == "propose":
            await self.agent.handle_propose(task_id, session_id, pipeline_run_id, sender, content)


class FinancialModellingAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )
        self.simpy_runs = int(os.getenv("SIMPY_SIMULATION_RUNS", "1000"))

    async def setup(self):
        logger.info("[FinancialModelling] Starting")
        self.add_behaviour(ListenBehaviour())

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

        # Run SimPy Monte Carlo simulation
        sim_assumptions = self._build_sim_assumptions(validated_input)
        logger.info("[FinancialModelling] Running SimPy with %d runs", self.simpy_runs)
        sim_results = run_simulation(sim_assumptions, num_runs=self.simpy_runs)
        logger.info("[FinancialModelling] SimPy complete — primary risk: %s", sim_results["primary_risk_factor"])

        # Load financial skills
        skills_loaded = []
        skills_content = ""
        for skill_name in validated_input.financial_skills:
            skill_text = _load_skill(skill_name)
            if skill_text:
                skills_content += f"\n\n--- SKILL: {skill_name} ---\n{skill_text}"
                skills_loaded.append(skill_name)

        # Call LLM with skills + sim results
        user_message = self._build_prompt(validated_input, sim_results, skills_content)
        llm_response = await self._call_llm(user_message)

        if not llm_response:
            await self._escalate(task_id, session_id, pipeline_run_id, "weak_evidence", "LLM call failed")
            return

        try:
            output_data = json.loads(llm_response)
            output_data["task_id"] = task_id
            output_data["model_used"] = self.model_id
            output_data["simpy_runs_completed"] = sim_results["runs_completed"]
            output_data["financial_skills_applied"] = skills_loaded
            output_data["probability_distribution"] = sim_results["probability_distribution"]
            output_data["primary_risk_factor"] = sim_results["primary_risk_factor"]
            validated_output = FinancialModellingOutput(**output_data)
        except Exception as e:
            logger.error("[FinancialModelling] Output validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "output_conflict", str(e))
            return

        result = validated_output.model_dump()
        self.redis.client.set(f"task_output:{task_id}", json.dumps(result), ex=3600)
        await self._send_inform(task_id, session_id, pipeline_run_id, result)

    async def handle_propose(self, task_id, session_id, pipeline_run_id, sender, content):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"status": "accepted", "proposal": content.get("proposal", "")})
        await self.send(msg)

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

    async def _call_llm(self, user_message: str) -> Optional[str]:
        try:
            response = self.bedrock.invoke_model(
                modelId=self.model_id,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 8192,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_message}],
                }),
            )
            result = json.loads(response["body"].read())
            return result["content"][0]["text"]
        except Exception as e:
            logger.error("[FinancialModelling] LLM call failed: %s", e)
            return None

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "12", "gap_key": "cost_structure"})
        await self.send(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "section_number": "12"})
        await self.send(msg)


async def main():
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
