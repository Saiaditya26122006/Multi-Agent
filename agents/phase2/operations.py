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
from schemas.inputs.operations import OperationsInput
from schemas.outputs.operations import OperationsOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Operations agent in a multi-agent business plan system.
Your role: define the production/delivery process, cost structure, capacity plan,
and optionally R&D plan and IP analysis if the business involves technology innovation.

Rules:
- production_process must be at least 50 characters
- cost_structure must include: fixed_costs, variable_costs, cogs_per_unit — each with source labels
- All cost items must be labelled with source (validated/assumed/benchmarked)
- If technology_description or ip_status are provided, include rd_plan and ip_analysis
- capacity_plan must address scalability — what happens at 2x and 5x volume
- If this is a pure services business, focus on delivery process rather than manufacturing
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


class OperationsAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )

    async def setup(self):
        logger.info("[Operations] Starting")
        self.add_behaviour(ListenBehaviour())

    async def handle_request(self, task_id, session_id, pipeline_run_id, content):
        task = content.get("task", {})
        input_package = task.get("input_package", {})

        try:
            validated_input = OperationsInput(
                task_id=task_id,
                session_id=session_id,
                opportunity_description=input_package.get("opportunity_description", ""),
                business_type=input_package.get("business_type", ""),
                revenue_assumptions=input_package.get("revenue_assumptions", {}),
                swot_matrix=input_package.get("swot_matrix", {}),
                technology_description=input_package.get("technology_description"),
                ip_status=input_package.get("ip_status"),
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[Operations] Input validation failed: %s", e)
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
            validated_output = OperationsOutput(**output_data)
        except Exception as e:
            logger.error("[Operations] Output validation failed: %s", e)
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

    def _build_prompt(self, inp: OperationsInput) -> str:
        tech_section = ""
        if inp.technology_description:
            tech_section = f"\nTECHNOLOGY: {inp.technology_description}\nIP STATUS: {inp.ip_status or 'Not provided'}"

        return f"""Define the operations and production plan for this business.

OPPORTUNITY: {inp.opportunity_description}
BUSINESS TYPE: {inp.business_type}
REVENUE ASSUMPTIONS: {json.dumps(inp.revenue_assumptions, indent=2)}
SWOT: {json.dumps(inp.swot_matrix, indent=2)}{tech_section}

Return ONLY valid JSON with these exact keys:
- section_number: "10"
- production_process: str (min 50 chars, how the product/service is made or delivered)
- cost_structure: {{"fixed_costs": {{"item": amount, ...}}, "variable_costs": {{"item": amount, ...}}, "cogs_per_unit": float, "source_labels": {{"item": "validated"|"assumed"|"benchmarked"}}}}
- capacity_plan: str (scalability at 2x and 5x volume)
- supplier_strategy: str|null
- rd_plan: str|null (include only if technology innovation is involved)
- ip_analysis: str|null (include only if IP is relevant)
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
            logger.error("[Operations] LLM call failed: %s", e)
            return None

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "10", "gap_key": "cost_structure"})
        await self.send(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "section_number": "10"})
        await self.send(msg)


async def main():
    jid = os.getenv("OPERATIONS_JID")
    password = os.getenv("OPERATIONS_PASSWORD")
    if not jid or not password:
        raise ValueError("OPERATIONS_JID and PASSWORD must be set")
    agent = OperationsAgent(jid=jid, password=password)
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
