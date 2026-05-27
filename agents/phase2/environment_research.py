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

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, pest_analysis, five_forces, risks_opportunities, market_context, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
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
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"))
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)

    async def setup(self):
        logger.info("[EnvironmentResearch] Starting")
        self.add_behaviour(ListenBehaviour())
        signal_ready(self.redis, "environment_research")

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

        cross_context = input_package.get("cross_section_context", {})
        learning_context = input_package.get("learning_context", "")

        revision_required = input_package.get("revision_required", False)
        revision_feedback = input_package.get("revision_feedback", "")
        if revision_required and revision_feedback:
            learning_context += f"\n\nMANDATORY REVISIONS (from quality review):\n{revision_feedback}\nFix these issues. Do NOT weaken your analysis — make it more rigorous."

        input_data = {
            "market_scope": input_package.get("market_scope", ""),
            "business_type": input_package.get("business_type", ""),
            "icp_hypothesis": input_package.get("icp_hypothesis", {}),
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

        parsed, reasoning_trace, token_usage = await self.intelligence.reason_and_produce(
            agent_role=(
                "Environment Research — you conduct PEST analysis, Porter's Five Forces, "
                "and identify external risks and opportunities for the business"
            ),
            input_data=input_data,
            output_schema_prompt=self._build_schema_prompt(),
            cross_section_context=cross_context if cross_context else None,
            reasoning_budget=3 if revision_required else 2,
            learning_context=learning_context,
        )

        if not parsed:
            user_message = self._build_prompt(validated_input)
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
            validated_output = EnvironmentResearchOutput(**parsed)
        except Exception as e:
            logger.error("[EnvironmentResearch] Output validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "output_conflict", str(e))
            return

        result = validated_output.model_dump()
        result["reasoning_trace"] = reasoning_trace
        self.redis.client.set(f"task_output:{task_id}", json.dumps(result, default=str), ex=3600)
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

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON with these exact keys:
- section_number: "3"
- pest_analysis: list of {"category": "political"|"economic"|"social"|"technological", "factor": str, "impact": "positive"|"negative"|"neutral", "relevance": "high"|"medium"|"low"} (min 4 items, one per category)
- five_forces: list of {"force": str, "assessment": str, "strength": "high"|"medium"|"low"} (min 5 items)
- risks_opportunities: {"risks": [str], "opportunities": [str]}
- market_context: str (min 100 chars, synthesized external environment summary)
- assumptions_used: list of {"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0"""

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

    def _parse_llm_response(self, raw: str, inp: EnvironmentResearchInput) -> dict:
        """Parse LLM response with retry before falling back to defaults."""
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(inp),
            agent_name="EnvironmentResearch",
        )
        if result is not None:
            return result

        logger.warning("[EnvironmentResearch] Both parse attempts failed, constructing fallback")
        return {
                "section_number": "3",
                "pest_analysis": [
                    {"category": "political", "factor": "Regulatory environment uncertain", "impact": "neutral", "relevance": "medium"},
                    {"category": "economic", "factor": "Market conditions for " + (inp.business_type or "new venture"), "impact": "neutral", "relevance": "high"},
                    {"category": "social", "factor": "Target demographic trends", "impact": "positive", "relevance": "medium"},
                    {"category": "technological", "factor": "Technology adoption in target market", "impact": "positive", "relevance": "high"},
                ],
                "five_forces": [
                    {"force": "Threat of new entrants", "assessment": "Moderate barriers to entry", "strength": "medium"},
                    {"force": "Bargaining power of suppliers", "assessment": "Multiple supplier options available", "strength": "low"},
                    {"force": "Bargaining power of buyers", "assessment": "Price-sensitive buyers with alternatives", "strength": "medium"},
                    {"force": "Threat of substitutes", "assessment": "Existing solutions partially address the need", "strength": "medium"},
                    {"force": "Industry rivalry", "assessment": "Competitive market with room for differentiation", "strength": "high"},
                ],
                "risks_opportunities": {"risks": ["Market timing uncertainty", "Competitive response"], "opportunities": ["First-mover advantage in niche", "Growing market demand"]},
                "market_context": f"The external environment for {inp.market_scope or 'this market'} presents moderate challenges and opportunities. Full analysis requires additional data points that were not available.",
                "assumptions_used": [{"statement": "LLM output was unparseable — defaults used", "confidence": "low", "source": "assumed", "source_detail": None}],
                "uncertainties": ["LLM response could not be parsed — full analysis not completed"],
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
                inferenceConfig={"maxTokens": 4096},
            )
            usage = response.get("usage", {})
            text = response["output"]["message"]["content"][0]["text"]
            return text, {"input_tokens": usage.get("inputTokens", 0), "output_tokens": usage.get("outputTokens", 0)}
        except Exception as e:
            logger.error("[EnvironmentResearch] LLM call failed: %s", e)
            return None, {}

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "3", "gap_key": "market_scope"})
        await self._send_msg(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "section_number": "3"})
        await self._send_msg(msg)


async def main():
    from dotenv import load_dotenv
    load_dotenv()
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
