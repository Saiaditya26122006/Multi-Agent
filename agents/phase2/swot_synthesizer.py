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
from schemas.inputs.swot_synthesizer import SWOTSynthesizerInput
from schemas.outputs.swot_synthesizer import SWOTSynthesizerOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the SWOT Synthesizer agent in a multi-agent business plan system.
Your role: combine external environment analysis (PEST, Five Forces) with internal
organisational analysis (capabilities, gaps) into a coherent SWOT matrix with strategic implications.

Rules:
- Each SWOT quadrant must have at least 2 items
- Each item must include evidence (not just assertion)
- strategic_implications must be at least 100 characters
- priority_strategic_issues must have at least 2 items
- Cross-reference: strengths should relate to capabilities, threats to five forces, etc.
- If Section 4 data is unavailable, derive weaknesses from opportunity gaps instead
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


class SWOTSynthesizerAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )

    async def setup(self):
        logger.info("[SWOTSynthesizer] Starting")
        self.add_behaviour(ListenBehaviour())

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
            validated_input = SWOTSynthesizerInput(
                task_id=task_id,
                session_id=session_id,
                pest_analysis=input_package.get("pest_analysis", []),
                five_forces=input_package.get("five_forces", []),
                risks_opportunities=input_package.get("risks_opportunities", {}),
                capability_gaps=input_package.get("capability_gaps", []),
                org_structure=input_package.get("org_structure", ""),
                opportunity_description=input_package.get("opportunity_description", ""),
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[SWOTSynthesizer] Input validation failed: %s", e)
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
            validated_output = SWOTSynthesizerOutput(**output_data)
        except Exception as e:
            logger.error("[SWOTSynthesizer] Output validation failed: %s", e)
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
        await self._send_msg(msg)

    def _build_prompt(self, inp: SWOTSynthesizerInput) -> str:
        return f"""Synthesize a SWOT matrix from these inputs.

PEST ANALYSIS: {json.dumps(inp.pest_analysis, indent=2)}
FIVE FORCES: {json.dumps(inp.five_forces, indent=2)}
RISKS & OPPORTUNITIES: {json.dumps(inp.risks_opportunities, indent=2)}
CAPABILITY GAPS: {json.dumps(inp.capability_gaps, indent=2)}
ORG STRUCTURE: {inp.org_structure}
OPPORTUNITY: {inp.opportunity_description}

Return ONLY valid JSON with these exact keys:
- section_number: "5"
- strengths: list of {{"item": str, "evidence": str, "impact": "high"|"medium"|"low"}} (min 2)
- weaknesses: list of {{"item": str, "evidence": str, "impact": "high"|"medium"|"low"}} (min 2)
- opportunities: list of {{"item": str, "evidence": str, "impact": "high"|"medium"|"low"}} (min 2)
- threats: list of {{"item": str, "evidence": str, "impact": "high"|"medium"|"low"}} (min 2)
- strategic_implications: str (min 100 chars, what the SWOT means for strategy)
- priority_strategic_issues: [str] (min 2 items, top issues to address)
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
            logger.error("[SWOTSynthesizer] LLM call failed: %s", e)
            return None

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "5", "gap_key": "pest_analysis"})
        await self._send_msg(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "section_number": "5"})
        await self._send_msg(msg)


async def main():
    jid = os.getenv("SWOT_SYNTHESIZER_JID")
    password = os.getenv("SWOT_SYNTHESIZER_PASSWORD")
    if not jid or not password:
        raise ValueError("SWOT_SYNTHESIZER_JID and PASSWORD must be set")
    agent = SWOTSynthesizerAgent(jid=jid, password=password)
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
