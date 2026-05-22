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
from schemas.inputs.environment_research import EnvironmentResearchInput
from schemas.outputs.environment_research import EnvironmentResearchOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Environment Research agent in a multi-agent business plan system.
Your role: conduct a PEST analysis, Porter's Five Forces assessment, and identify key risks
and opportunities in the external business environment.

Rules:
- PEST must cover all 4 categories with at least one factor each
- Five Forces must cover all 5 forces
- Market context must be at least 100 characters of substantive analysis
- Every assumption must be labelled with confidence and source
- If data is unavailable, state what is unknown rather than fabricating
- Focus on factors relevant to the specific market_scope and business_type provided
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


class EnvironmentResearchAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )

    async def setup(self):
        logger.info("[EnvironmentResearch] Starting")
        self.add_behaviour(ListenBehaviour())

    async def handle_request(self, task_id, session_id, pipeline_run_id, content):
        task = content.get("task", {})
        input_package = task.get("input_package", {})

        try:
            validated_input = EnvironmentResearchInput(
                task_id=task_id,
                session_id=session_id,
                market_scope=input_package.get("market_scope", ""),
                business_type=input_package.get("business_type", ""),
                icp_hypothesis=input_package.get("icp_hypothesis", {}),
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[EnvironmentResearch] Input validation failed: %s", e)
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
            validated_output = EnvironmentResearchOutput(**output_data)
        except Exception as e:
            logger.error("[EnvironmentResearch] Output validation failed: %s", e)
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

    def _build_prompt(self, inp: EnvironmentResearchInput) -> str:
        return f"""Conduct environment research for this business.

MARKET SCOPE: {inp.market_scope}
BUSINESS TYPE: {inp.business_type}
ICP HYPOTHESIS: {json.dumps(inp.icp_hypothesis, indent=2)}

Return ONLY valid JSON with these exact keys:
- section_number: "3"
- pest_analysis: list of {{"category": "political"|"economic"|"social"|"technological", "factor": str, "impact": "positive"|"negative"|"neutral", "relevance": "high"|"medium"|"low"}} (min 4 items)
- five_forces: list of {{"force": str, "assessment": str, "strength": "high"|"medium"|"low"}} (min 5 items)
- risks_opportunities: {{"risks": [str], "opportunities": [str]}}
- market_context: str (min 100 chars, overall external environment summary)
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
            logger.error("[EnvironmentResearch] LLM call failed: %s", e)
            return None

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "3", "gap_key": "market_scope"})
        await self.send(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "section_number": "3"})
        await self.send(msg)


async def main():
    jid = os.getenv("ENVIRONMENT_RESEARCH_JID")
    password = os.getenv("ENVIRONMENT_RESEARCH_PASSWORD")
    if not jid or not password:
        raise ValueError("ENVIRONMENT_RESEARCH_JID and PASSWORD must be set")
    agent = EnvironmentResearchAgent(jid=jid, password=password)
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
