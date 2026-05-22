import asyncio
import json
import logging
import os
from typing import Optional

import boto3
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour
from spade.message import Message

from memory.redis_client import RedisClient
from schemas.inputs.launch_contingency import LaunchContingencyInput
from schemas.outputs.launch_contingency import LaunchContingencyOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Launch & Contingency agent in a multi-agent business plan system.
Your role: build the start-up programme (sequenced milestones to first customer) and
contingency plan (what happens if key assumptions fail).

Rules:
- launch_programme must have at least 3 milestones with target_date_months, responsible, success_metric
- prerequisite_conditions must have at least 2 items
- capital_plan must be at least 50 characters explaining how and when to raise capital
- critical_path_item must identify the single most important thing to get right first
- contingency_scenarios should be included when probability_distribution shows >30% cash-out rate
- exit_conditions should define when to wind down — not if, but when the trigger fires
- Milestones must be sequenced logically with dependencies
"""


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


class LaunchContingencyAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )

    async def setup(self):
        logger.info("[LaunchContingency] Starting")
        self.add_behaviour(ListenBehaviour())

    async def handle_request(self, task_id, session_id, pipeline_run_id, content):
        task = content.get("task", {})
        input_package = task.get("input_package", {})

        try:
            validated_input = LaunchContingencyInput(
                task_id=task_id,
                session_id=session_id,
                revenue_assumptions=input_package.get("revenue_assumptions", {}),
                headcount_plan=input_package.get("headcount_plan", {}),
                break_even_analysis=input_package.get("break_even_analysis", {}),
                probability_distribution=input_package.get("probability_distribution", []),
                primary_risk_factor=input_package.get("primary_risk_factor", ""),
                market_entry_strategy=input_package.get("market_entry_strategy", ""),
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[LaunchContingency] Input validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "unclear_input", str(e))
            return

        user_message = self._build_prompt(validated_input)
        llm_response = await self._call_llm(user_message)

        if not llm_response:
            await self._escalate(task_id, session_id, pipeline_run_id, "weak_evidence", "LLM call failed")
            return

        try:
            output_data = json.loads(llm_response)
            output_data["task_id"] = task_id
            output_data["model_used"] = self.model_id
            validated_output = LaunchContingencyOutput(**output_data)
        except Exception as e:
            logger.error("[LaunchContingency] Output validation failed: %s", e)
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

    def _build_prompt(self, inp: LaunchContingencyInput) -> str:
        return f"""Build the start-up programme and contingency plan.

REVENUE ASSUMPTIONS: {json.dumps(inp.revenue_assumptions, indent=2)}
HEADCOUNT PLAN: {json.dumps(inp.headcount_plan, indent=2)}
BREAK-EVEN ANALYSIS: {json.dumps(inp.break_even_analysis, indent=2)}
SIMULATION RESULTS (P10/P50/P90): {json.dumps(inp.probability_distribution, indent=2)}
PRIMARY RISK FACTOR: {inp.primary_risk_factor}
MARKET ENTRY STRATEGY: {inp.market_entry_strategy}

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

    async def _call_llm(self, user_message: str) -> Optional[str]:
        try:
            response = self.bedrock.invoke_model(
                modelId=self.model_id,
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 4096,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_message}],
                }),
            )
            result = json.loads(response["body"].read())
            return result["content"][0]["text"]
        except Exception as e:
            logger.error("[LaunchContingency] LLM call failed: %s", e)
            return None

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "13", "gap_key": "budget_constraints"})
        await self.send(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "section_number": "13"})
        await self.send(msg)


async def main():
    jid = os.getenv("LAUNCH_CONTINGENCY_JID")
    password = os.getenv("LAUNCH_CONTINGENCY_PASSWORD")
    if not jid or not password:
        raise ValueError("LAUNCH_CONTINGENCY_JID and PASSWORD must be set")
    agent = LaunchContingencyAgent(jid=jid, password=password)
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
