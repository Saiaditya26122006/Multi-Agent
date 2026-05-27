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

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, strengths, weaknesses, opportunities, threats, strategic_implications, priority_strategic_issues, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
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
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"))
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)

    async def setup(self):
        logger.info("[SWOTSynthesizer] Starting")
        self.add_behaviour(ListenBehaviour())
        signal_ready(self.redis, "swot_synthesizer")

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

        cross_context = input_package.get("cross_section_context", {})
        input_data = {
            "pest_analysis": input_package.get("pest_analysis", []),
            "five_forces": input_package.get("five_forces", []),
            "risks_opportunities": input_package.get("risks_opportunities", {}),
            "capability_gaps": input_package.get("capability_gaps", []),
            "org_structure": input_package.get("org_structure", ""),
            "opportunity_description": input_package.get("opportunity_description", ""),
        }

        parsed, reasoning_trace, token_usage = await self.intelligence.reason_and_produce(
            agent_role=(
                "SWOT Synthesizer — you combine PEST analysis, Five Forces, and org capabilities "
                "into a coherent SWOT matrix with strategic implications and priority issues"
            ),
            input_data=input_data,
            output_schema_prompt=self._build_schema_prompt(),
            cross_section_context=cross_context if cross_context else None,
            reasoning_budget=3,
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
            validated_output = SWOTSynthesizerOutput(**parsed)
        except Exception as e:
            logger.error("[SWOTSynthesizer] Output validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "output_conflict", str(e))
            return

        result = validated_output.model_dump()
        result["reasoning_trace"] = reasoning_trace
        self.redis.client.set(f"task_output:{task_id}", json.dumps(result, default=str), ex=3600)

        await self._check_contradictions(task_id, session_id, pipeline_run_id, validated_input, result)
        await self._send_inform(task_id, session_id, pipeline_run_id, result)

    async def _check_contradictions(self, task_id, session_id, pipeline_run_id, inp, output):
        """Detect if SWOT threats are unaddressed by org capabilities."""
        threats = output.get("threats", [])
        high_threats = [t for t in threats if isinstance(t, dict) and t.get("impact") == "high"]
        capability_gaps = inp.capability_gaps or []
        gap_descriptions = [g.get("gap", "").lower() if isinstance(g, dict) else "" for g in capability_gaps]

        unaddressed = []
        for threat in high_threats:
            item = threat.get("item", "").lower()
            addressed = any(gap in item or item in gap for gap in gap_descriptions if gap)
            if not addressed:
                unaddressed.append(threat.get("item", ""))

        if len(unaddressed) >= 2:
            mother_jid = os.getenv("MOTHER_AGENT_JID", "")
            msg = Message(to=mother_jid)
            msg.set_metadata("performative", "propose")
            msg.set_metadata("task_id", task_id)
            msg.set_metadata("session_id", session_id)
            msg.set_metadata("pipeline_run_id", pipeline_run_id)
            msg.body = json.dumps({
                "target_agent": "organisation_designer",
                "proposal": f"SWOT found {len(unaddressed)} high-severity threats with no matching "
                            f"capability in org structure: {', '.join(unaddressed[:3])}. "
                            f"Suggest adding capability_gaps or roles to address these.",
                "field": "capability_gaps",
                "evidence": {"unaddressed_threats": unaddressed[:3]},
            })
            await self._send_msg(msg)
            logger.info("[SWOTSynthesizer] Proposed capability gap addition to org designer")

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
- section_number: "5"
- strengths: list of {"item": str, "evidence": str, "impact": "high"|"medium"|"low"} (min 2)
- weaknesses: list of {"item": str, "evidence": str, "impact": "high"|"medium"|"low"} (min 2)
- opportunities: list of {"item": str, "evidence": str, "impact": "high"|"medium"|"low"} (min 2)
- threats: list of {"item": str, "evidence": str, "impact": "high"|"medium"|"low"} (min 2)
- strategic_implications: str (min 100 chars — what the SWOT means for the business strategy)
- priority_strategic_issues: [str] (min 2 items — top issues requiring immediate attention)
- assumptions_used: list of {"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0"""

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

    def _parse_llm_response(self, raw: str, inp: SWOTSynthesizerInput) -> dict:
        """Parse LLM response with retry before falling back to defaults."""
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(inp),
            agent_name="SWOTSynthesizer",
        )
        if result is not None:
            return result

        logger.warning("[SWOTSynthesizer] Both parse attempts failed, constructing fallback")
        return {
                "section_number": "5",
                "strengths": [
                    {"item": "Novel approach to market problem", "evidence": "Derived from opportunity description", "impact": "high"},
                    {"item": "Founder domain knowledge", "evidence": "CEO-provided assumptions", "impact": "medium"},
                ],
                "weaknesses": [
                    {"item": "Early stage with limited resources", "evidence": "Startup phase with capability gaps", "impact": "high"},
                    {"item": "Unproven business model", "evidence": "No validated revenue data", "impact": "medium"},
                ],
                "opportunities": [
                    {"item": "Growing market demand", "evidence": "Market context analysis", "impact": "high"},
                    {"item": "Technology adoption trend", "evidence": "PEST technological factors", "impact": "medium"},
                ],
                "threats": [
                    {"item": "Competitive response from incumbents", "evidence": "Five forces rivalry assessment", "impact": "high"},
                    {"item": "Market timing risk", "evidence": "Economic uncertainty", "impact": "medium"},
                ],
                "strategic_implications": "The business has a viable opportunity but must move quickly to establish a competitive position before incumbents respond. Resource constraints require focused execution on highest-impact activities.",
                "priority_strategic_issues": ["Validate product-market fit before scaling", "Secure initial funding to address capability gaps"],
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
            logger.error("[SWOTSynthesizer] LLM call failed: %s", e)
            return None, {}

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
    from dotenv import load_dotenv
    load_dotenv()
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
