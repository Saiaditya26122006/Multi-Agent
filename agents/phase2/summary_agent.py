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
from schemas.inputs.summary_agent import SummaryAgentInput
from schemas.outputs.summary_agent import SummaryAgentOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Summary Agent in a multi-agent business plan system.
Your role: synthesize all completed section outputs into a one-page executive summary
that Alex (the CEO) can use to make decisions or present to investors.

Rules:
- executive_summary must be 200-3000 characters
- It must cover: opportunity, competitive advantage, team, financials, ask
- headline_metrics must include: year1_revenue_range, break_even_month, primary_risk, team_size_year1
- Flag any assumptions that are labelled "assumed" or "low confidence" for Alex to validate
- List which sections were included vs skipped
- If coherence issues were found during the pipeline, list how they were resolved
- Write for Alex — plain language, no jargon, decision-oriented
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


class SummaryAgentAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )

    async def setup(self):
        logger.info("[SummaryAgent] Starting")
        self.add_behaviour(ListenBehaviour())

    async def handle_request(self, task_id, session_id, pipeline_run_id, content):
        task = content.get("task", {})
        input_package = task.get("input_package", {})

        try:
            validated_input = SummaryAgentInput(
                task_id=task_id,
                session_id=session_id,
                pipeline_run_id=pipeline_run_id,
                completed_sections=input_package.get("completed_sections", {}),
                flagged_assumptions=input_package.get("flagged_assumptions", []),
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[SummaryAgent] Input validation failed: %s", e)
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
            validated_output = SummaryAgentOutput(**output_data)
        except Exception as e:
            logger.error("[SummaryAgent] Output validation failed: %s", e)
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

    def _build_prompt(self, inp: SummaryAgentInput) -> str:
        all_sections = list(inp.completed_sections.keys())
        all_possible = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14"]
        skipped = [s for s in all_possible if s not in all_sections]

        return f"""Write an executive summary for this business plan.

COMPLETED SECTIONS ({len(all_sections)} total):
{json.dumps(inp.completed_sections, indent=2)}

FLAGGED ASSUMPTIONS (low confidence or assumed):
{json.dumps(inp.flagged_assumptions, indent=2)}

SECTIONS INCLUDED: {all_sections}
SECTIONS SKIPPED: {skipped}

Return ONLY valid JSON with these exact keys:
- section_number: "executive_summary"
- executive_summary: str (200-3000 chars. Cover: opportunity, competitive advantage, team, financials, ask. Write for a CEO — plain language, decision-oriented.)
- headline_metrics: {{"year1_revenue_range": str, "break_even_month": str, "primary_risk": str, "team_size_year1": str}}
- key_assumptions_flagged: [str] (assumptions Alex must validate before using this externally)
- sections_included: [str]
- sections_skipped: [str]
- coherence_issues_resolved: [str]
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
            logger.error("[SummaryAgent] LLM call failed: %s", e)
            return None

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "executive_summary", "gap_key": ""})
        await self.send(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "section_number": "executive_summary"})
        await self.send(msg)


async def main():
    jid = os.getenv("SUMMARY_JID")
    password = os.getenv("SUMMARY_PASSWORD")
    if not jid or not password:
        raise ValueError("SUMMARY_JID and PASSWORD must be set")
    agent = SummaryAgentAgent(jid=jid, password=password)
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
