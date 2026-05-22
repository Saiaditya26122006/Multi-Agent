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
from schemas.inputs.marketing_strategy import MarketingStrategyInput
from schemas.outputs.marketing_strategy import MarketingStrategyOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Marketing Strategy agent in a multi-agent business plan system.
Your role: build the full marketing plan including target market analysis, competitive positioning,
marketing mix (product/price/distribution/promotion), customer relations strategy, and critically,
the revenue and CAC assumptions that feed the financial model.

Rules:
- competitors list must have at least 2 entries
- competitive_advantages must have at least 2 entries
- revenue_assumptions must include: price_per_unit, volume_year1, volume_year2, volume_year3, sales_cycle_months
- cac_assumptions must include: cac_estimate, cac_source, confidence
- market_entry_strategy must be at least 50 characters
- If pricing data is unavailable from CEO, infer from competitive analysis and label as agent_inferred
- Never claim "no competitors" — always identify substitutes at minimum
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


class MarketingStrategyAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )

    async def setup(self):
        logger.info("[MarketingStrategy] Starting")
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
            validated_input = MarketingStrategyInput(
                task_id=task_id,
                session_id=session_id,
                swot_matrix=input_package.get("swot_matrix", {}),
                icp_hypothesis=input_package.get("icp_hypothesis", {}),
                competitive_strategy=input_package.get("competitive_strategy", ""),
                market_context=input_package.get("market_context", ""),
                strategic_implications=input_package.get("strategic_implications", ""),
                pricing_assumption=input_package.get("pricing_assumption"),
                target_volume=input_package.get("target_volume"),
                cac_assumptions=input_package.get("cac_assumptions"),
                partnership_targets=input_package.get("partnership_targets"),
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[MarketingStrategy] Input validation failed: %s", e)
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
            validated_output = MarketingStrategyOutput(**output_data)
        except Exception as e:
            logger.error("[MarketingStrategy] Output validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "output_conflict", str(e))
            return

        result = validated_output.model_dump()
        self.redis.client.set(f"task_output:{task_id}", json.dumps(result), ex=3600)
        await self._send_inform(task_id, session_id, pipeline_run_id, result)

    async def handle_propose(self, task_id, session_id, pipeline_run_id, sender, content):
        proposal = content.get("proposal", "")
        field = content.get("field", "")
        if field in ("revenue_assumptions", "cac_assumptions"):
            mother_jid = os.getenv("MOTHER_AGENT_JID", "")
            msg = Message(to=mother_jid)
            msg.set_metadata("performative", "refuse")
            msg.set_metadata("task_id", task_id)
            msg.set_metadata("session_id", session_id)
            msg.set_metadata("pipeline_run_id", pipeline_run_id)
            msg.body = json.dumps({
                "original_proposer": sender,
                "reason": "Revenue/CAC assumptions are derived from market analysis — cannot accept external override without evidence",
            })
            await self._send_msg(msg)
        else:
            mother_jid = os.getenv("MOTHER_AGENT_JID", "")
            msg = Message(to=mother_jid)
            msg.set_metadata("performative", "inform")
            msg.set_metadata("task_id", task_id)
            msg.set_metadata("session_id", session_id)
            msg.set_metadata("pipeline_run_id", pipeline_run_id)
            msg.body = json.dumps({"status": "accepted", "proposal": proposal})
            await self._send_msg(msg)

    def _build_prompt(self, inp: MarketingStrategyInput) -> str:
        return f"""Build a complete marketing strategy for this business.

SWOT MATRIX: {json.dumps(inp.swot_matrix, indent=2)}
ICP HYPOTHESIS: {json.dumps(inp.icp_hypothesis, indent=2)}
COMPETITIVE STRATEGY: {inp.competitive_strategy}
MARKET CONTEXT: {inp.market_context}
STRATEGIC IMPLICATIONS: {inp.strategic_implications}
PRICING ASSUMPTION (from CEO): {inp.pricing_assumption or 'Not provided — infer from competitors'}
TARGET VOLUME (from CEO): {inp.target_volume or 'Not provided — derive from market size'}
CAC ASSUMPTION (from CEO): {inp.cac_assumptions or 'Not provided — benchmark from industry'}
PARTNERSHIP TARGETS: {json.dumps(inp.partnership_targets) if inp.partnership_targets else 'None specified'}

Return ONLY valid JSON with these exact keys:
- section_number: "8"
- target_market_analysis: {{"segmentation": str, "icp_refined": str, "market_size_tam_sam_som": str}}
- competitors: list of {{"name": str, "positioning": str, "pricing": str|null, "strengths": [str], "weaknesses": [str]}} (min 2)
- competitive_advantages: [str] (min 2)
- marketing_mix: {{"product": str, "pricing_policy": str, "distribution": str, "promotion": str}}
- customer_relations: {{"communication": str, "loyalty_strategy": str}}
- revenue_assumptions: {{"price_per_unit": float, "volume_year1": int, "volume_year2": int, "volume_year3": int, "sales_cycle_months": int}}
- cac_assumptions: {{"cac_estimate": float, "cac_source": str, "confidence": "high"|"medium"|"low"}}
- market_entry_strategy: str (min 50 chars)
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
            logger.error("[MarketingStrategy] LLM call failed: %s", e)
            return None

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "8", "gap_key": "pricing_assumption"})
        await self._send_msg(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "section_number": "8"})
        await self._send_msg(msg)


async def main():
    jid = os.getenv("MARKETING_STRATEGY_JID")
    password = os.getenv("MARKETING_STRATEGY_PASSWORD")
    if not jid or not password:
        raise ValueError("MARKETING_STRATEGY_JID and PASSWORD must be set")
    agent = MarketingStrategyAgent(jid=jid, password=password)
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
