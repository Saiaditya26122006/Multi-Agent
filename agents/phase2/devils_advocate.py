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

from agents.phase2.rag_mixin import rag_enrich, rag_check_killed
from memory.redis_client import RedisClient
from agents.phase2.llm_utils import strip_markdown_json, signal_ready
from schemas.inputs.devils_advocate import DevilsAdvocateInput
from schemas.outputs.devils_advocate import DevilsAdvocateOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Devil's Advocate in a multi-agent business plan system.
Your job: ruthlessly challenge every section output before it gets delivered to the CEO.

You are NOT trying to be helpful. You are trying to find every weakness, overconfidence,
logical gap, math error, and unsupported claim. You represent the skeptical investor who
will tear this plan apart.

Rules:
- Challenge SPECIFIC claims — not vague "this could be better"
- Every challenge must cite the exact text or number being questioned
- "Overconfidence" means the section claims high/medium confidence but evidence is weak
- "Logical gap" means A doesn't actually lead to B even though the section claims it does
- "Math error" means numbers don't add up or are inconsistent
- "Contradiction" means this section conflicts with another completed section
- "Survivorship bias" means the section assumes best-case without acknowledging failure modes
- Be brutal but fair — if something is genuinely well-reasoned, say so

Verdict rules:
- "pass" — fewer than 2 issues total, none high-severity
- "revise" — 2+ medium issues or 1+ high-severity issue that can be fixed
- "reject" — multiple high-severity issues indicating fundamental problems

You must respond with ONLY a valid JSON object matching the schema exactly."""


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

        if performative == "request":
            await self.agent.handle_request(task_id, session_id, pipeline_run_id, content)


class DevilsAdvocateAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"))

    async def setup(self):
        logger.info("[DevilsAdvocate] Starting")
        self.add_behaviour(ListenBehaviour())
        signal_ready(self.redis, "devils_advocate")

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
            validated_input = DevilsAdvocateInput(
                task_id=task_id,
                session_id=session_id,
                pipeline_run_id=pipeline_run_id,
                section_number=input_package.get("section_number", ""),
                section_output=input_package.get("section_output", {}),
                reasoning_trace=input_package.get("reasoning_trace", {}),
                cross_section_context=input_package.get("cross_section_context", {}),
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[DevilsAdvocate] Input validation failed: %s", e)
            escalation = self._fallback_escalate(task_id, input_package.get("section_number", "unknown"), f"Input validation failed: {str(e)}")
            await self._escalate_to_mother(task_id, session_id, pipeline_run_id, escalation)
            return

        # RAG: Get resolved contradictions so DA doesn't re-raise them
        rag_context = rag_enrich(
            "contradictions resolved decisions prohibited claims",
            section=validated_input.section_number,
            source_types=["contradiction_resolution", "decision", "negative_knowledge", "ceo_doc"],
        )
        if rag_context:
            validated_input.cross_section_context["rag_resolved_contradictions"] = rag_context

        user_message = self._build_prompt(validated_input)
        llm_response, token_usage = await self._call_llm(user_message)

        if not llm_response:
            logger.warning("[DevilsAdvocate] LLM failed — escalating")
            escalation = self._fallback_escalate(task_id, validated_input.section_number, "LLM call failed")
            await self._escalate_to_mother(task_id, session_id, pipeline_run_id, escalation)
            return

        parsed = self._parse_response(llm_response)
        if not parsed:
            logger.warning("[DevilsAdvocate] Parse failed — escalating")
            escalation = self._fallback_escalate(task_id, validated_input.section_number, "LLM response could not be parsed")
            await self._escalate_to_mother(task_id, session_id, pipeline_run_id, escalation)
            return

        try:
            parsed["task_id"] = task_id
            parsed["section_number"] = validated_input.section_number
            parsed["model_used"] = self.model_id
            parsed["input_tokens"] = token_usage.get("input_tokens", 0)
            parsed["output_tokens"] = token_usage.get("output_tokens", 0)
            validated_output = DevilsAdvocateOutput(**parsed)
        except Exception as e:
            logger.error("[DevilsAdvocate] Output validation failed: %s", e)
            escalation = self._fallback_escalate(task_id, validated_input.section_number, f"Output validation failed: {str(e)}")
            await self._escalate_to_mother(task_id, session_id, pipeline_run_id, escalation)
            return

        result = validated_output.model_dump()
        self.redis.client.set(f"devils_advocate:{task_id}", json.dumps(result, default=str), ex=3600)

        logger.info(
            "[DevilsAdvocate] Section %s verdict: %s (%d challenges)",
            validated_input.section_number,
            result["verdict"],
            len(result["challenges"]),
        )

        await self._send_inform(task_id, session_id, pipeline_run_id, result)

    def _build_prompt(self, inp: DevilsAdvocateInput) -> str:
        section_str = json.dumps(inp.section_output, indent=2, default=str)[:6000]
        trace_str = json.dumps(inp.reasoning_trace, indent=2, default=str)[:2000] if inp.reasoning_trace else "No reasoning trace available"

        cross_context_str = ""
        if inp.cross_section_context:
            summaries = []
            for sec, data in inp.cross_section_context.items():
                if isinstance(data, dict):
                    key_fields = {k: v for k, v in data.items()
                                  if k in ("confidence_score", "revenue_assumptions",
                                           "break_even_analysis", "strategic_implications",
                                           "headcount_plan", "opportunity_description")}
                    if key_fields:
                        summaries.append(f"Section {sec}: {json.dumps(key_fields, default=str)[:400]}")
            if summaries:
                cross_context_str = "\n\nOTHER COMPLETED SECTIONS (check for contradictions):\n" + "\n".join(summaries[:5])

        return f"""Challenge this business plan section. Find every weakness.

SECTION NUMBER: {inp.section_number}

SECTION OUTPUT:
{section_str}

REASONING TRACE (how the agent arrived at this):
{trace_str}
{cross_context_str}

ACCEPTANCE CRITERIA: {inp.acceptance_criteria}

Check for:
1. LOGICAL GAPS — Does the reasoning chain hold? Does A actually lead to B?
2. OVERCONFIDENCE — Is confidence "high" where evidence is thin? Are assumptions labelled "validated" when they're really "assumed"?
3. MATH ERRORS — Do numbers add up? Are growth rates internally consistent?
4. CONTRADICTIONS — Does this conflict with other sections listed above?
5. SURVIVORSHIP BIAS — Is this assuming best-case without failure mode analysis?
6. UNSUPPORTED CLAIMS — Are there assertions without evidence or reasoning?

Return ONLY valid JSON with these exact keys:
- verdict: "pass"|"revise"|"reject"
- challenges: list of {{"claim": str, "challenge_type": "logical_gap"|"overconfidence"|"unsupported"|"math_error"|"contradiction"|"survivorship_bias", "severity": "high"|"medium"|"low", "explanation": str (min 20 chars), "suggested_fix": str, "section_reference": str|null}}
- confidence_assessment: "honest"|"inflated"|"deflated"
- recommended_confidence: "high"|"medium"|"low"
- assumptions_grade: "well_sourced"|"mixed"|"mostly_unsupported"
- overall_reasoning_quality: "strong"|"adequate"|"weak"
- summary: str (min 50 chars — one paragraph for the orchestrator)
- input_tokens: 0
- output_tokens: 0"""

    def _parse_response(self, raw: str) -> Optional[dict]:
        text = strip_markdown_json(raw)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("[DevilsAdvocate] Failed to parse response")
            return None

    def _fallback_escalate(self, task_id: str, section_number: str, reason: str) -> dict:
        """If Devil's Advocate fails, escalate to Mother — do NOT pass through."""
        return {
            "task_id": task_id,
            "section_number": section_number,
            "verdict": "escalate",
            "challenges": [{
                "claim": "Devil's Advocate quality gate failed",
                "challenge_type": "system_failure",
                "severity": "high",
                "explanation": f"Quality review could not be completed: {reason}. Manual review required before proceeding.",
                "suggested_fix": "Manual review required — quality gate failure indicates system issue",
                "section_reference": None,
            }],
            "confidence_assessment": "unknown",
            "recommended_confidence": "low",
            "assumptions_grade": "unknown",
            "overall_reasoning_quality": "unknown",
            "summary": f"ESCALATION REQUIRED: Devil's Advocate failed ({reason}). Section must be manually reviewed before downstream agents can proceed.",
            "model_used": self.model_id,
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
            logger.error("[DevilsAdvocate] LLM call failed: %s", e)
            return None, {}

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "agent": "devils_advocate"})
        await self._send_msg(msg)

    async def _escalate_to_mother(self, task_id, session_id, pipeline_run_id, output):
        """Send escalation to Mother, who pauses pipeline and notifies CEO."""
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({
            "trigger": "quality_gate_failure",
            "agent": "devils_advocate",
            "section_number": output.get("section_number", "unknown"),
            "output": output,
            "notes": output.get("summary", "Quality gate failure — manual review required"),
        })
        await self._send_msg(msg)
        logger.error(
            "[DevilsAdvocate] ESCALATED to Mother — Section %s quality gate failed",
            output.get("section_number", "unknown"),
        )


async def main():
    from dotenv import load_dotenv
    load_dotenv()
    jid = os.getenv("DEVILS_ADVOCATE_JID")
    password = os.getenv("DEVILS_ADVOCATE_PASSWORD")
    if not jid or not password:
        raise ValueError("DEVILS_ADVOCATE_JID and PASSWORD must be set")
    agent = DevilsAdvocateAgent(jid=jid, password=password)
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
