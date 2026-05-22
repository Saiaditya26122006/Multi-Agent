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
from schemas.inputs.opportunity_analyst import OpportunityAnalystInput
from schemas.outputs.opportunity_analyst import OpportunityAnalystOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Opportunity Analyst agent in a multi-agent business plan system.
Your role: analyse a business idea and produce a structured assessment of the opportunity,
competitive strategy, Year 1 objectives, and ideal customer profile hypothesis.

You receive the raw idea from Phase 1, the CEO's clarifying answers, and the approved decision.
You must produce structured JSON output matching the required schema exactly.

Rules:
- Every assumption must be labelled with confidence (high/medium/low) and source
- Objectives must be quantified with specific metrics
- ICP must include: buyer_role, budget_process, decision_timeline, pain_points
- If information is missing, label it as an uncertainty — do not fabricate
- competitive_strategy must be at least 30 characters of substance
- opportunity_description must be at least 50 characters
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


class OpportunityAnalystAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )

    async def setup(self):
        logger.info("[OpportunityAnalyst] Starting")
        self.add_behaviour(ListenBehaviour())

    async def _send_msg(self, msg: Message):
        class _Send(OneShotBehaviour):
            async def run(self_b):
                await self_b.send(msg)
        b = _Send()
        self.add_behaviour(b)
        await b.join(timeout=10)

    async def handle_request(
        self,
        task_id: str,
        session_id: str,
        pipeline_run_id: str,
        content: dict,
    ):
        """Process a task request from the Mother Agent."""
        task = content.get("task", {})
        input_package = task.get("input_package", {})

        try:
            validated_input = OpportunityAnalystInput(
                task_id=task_id,
                session_id=session_id,
                idea_summary=input_package.get("idea_summary", ""),
                ceo_assumptions=input_package.get("ceo_assumptions", []),
                approved_decision=input_package.get("approved_decision", {}),
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[OpportunityAnalyst] Input validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "unclear_input", str(e))
            return

        user_message = self._build_prompt(validated_input)
        llm_response = await self._call_llm(user_message)

        if not llm_response:
            await self._escalate(
                task_id, session_id, pipeline_run_id,
                "weak_evidence", "LLM call failed or returned empty",
            )
            return

        try:
            output_data = json.loads(llm_response)
            output_data["task_id"] = task_id
            output_data["model_used"] = self.model_id
            validated_output = OpportunityAnalystOutput(**output_data)
        except Exception as e:
            logger.error("[OpportunityAnalyst] Output validation failed: %s", e)
            await self._escalate(
                task_id, session_id, pipeline_run_id,
                "output_conflict", f"Schema validation failed: {e}",
            )
            return

        result = validated_output.model_dump()
        self.redis.client.set(f"task_output:{task_id}", json.dumps(result), ex=3600)

        await self._send_inform(task_id, session_id, pipeline_run_id, result)

    async def handle_propose(
        self,
        task_id: str,
        session_id: str,
        pipeline_run_id: str,
        sender: str,
        content: dict,
    ):
        """Handle a contradiction proposal from another agent."""
        proposal = content.get("proposal", "")
        section = content.get("section", "")

        agree = self._evaluate_proposal(proposal)

        if agree:
            await self._send_response(
                task_id, session_id, pipeline_run_id,
                "inform", {"status": "accepted", "proposal": proposal},
            )
        else:
            await self._send_response(
                task_id, session_id, pipeline_run_id,
                "refuse", {
                    "original_proposer": sender,
                    "reason": "Proposal conflicts with validated assumptions from Phase 1 data",
                },
            )

    def _build_prompt(self, inp: OpportunityAnalystInput) -> str:
        return f"""Analyse this business idea and produce a structured JSON output.

IDEA: {inp.idea_summary}

CEO Q&A (clarifying assumptions):
{json.dumps(inp.ceo_assumptions, indent=2)}

APPROVED DECISION FROM GATE 1:
{json.dumps(inp.approved_decision, indent=2)}

ACCEPTANCE CRITERIA: {inp.acceptance_criteria}

Return ONLY valid JSON with these exact keys:
- section_number: "1"
- opportunity_description: (min 50 chars) structured description of the opportunity
- competitive_strategy: (min 30 chars) how the business will compete
- objectives: list of dicts with "objective", "metric", "target_value", "timeframe"
- icp_hypothesis: dict with "buyer_role", "budget_process", "decision_timeline", "pain_points"
- assumptions_used: list of {{"statement", "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail"}}
- uncertainties: list of strings
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
            logger.error("[OpportunityAnalyst] LLM call failed: %s", e)
            return None

    def _evaluate_proposal(self, proposal: str) -> bool:
        return True

    async def _escalate(
        self,
        task_id: str,
        session_id: str,
        pipeline_run_id: str,
        trigger: str,
        notes: str,
    ):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({
            "trigger": trigger,
            "notes": notes,
            "section": "1",
            "gap_key": "idea_summary",
        })
        await self._send_msg(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "section_number": "1"})
        await self._send_msg(msg)

    async def _send_response(self, task_id, session_id, pipeline_run_id, performative, content):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", performative)
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps(content)
        await self._send_msg(msg)


async def main():
    jid = os.getenv("OPPORTUNITY_ANALYST_JID")
    password = os.getenv("OPPORTUNITY_ANALYST_PASSWORD")
    if not jid or not password:
        raise ValueError("OPPORTUNITY_ANALYST_JID and PASSWORD must be set")
    agent = OpportunityAnalystAgent(jid=jid, password=password)
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
