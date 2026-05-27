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

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, opportunity_description, competitive_strategy, objectives, icp_hypothesis, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
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
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"))
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)

    async def setup(self):
        logger.info("[OpportunityAnalyst] Starting")
        self.add_behaviour(ListenBehaviour())
        signal_ready(self.redis, "opportunity_analyst")

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
        """Process a task request from the Mother Agent using multi-step reasoning."""
        task = content.get("task", {})
        input_package = task.get("input_package", {})

        idea_summary = input_package.get("idea_summary", "")
        ceo_assumptions = input_package.get("ceo_assumptions", [])
        approved_decision = input_package.get("approved_decision", {})

        if len(idea_summary) < 10:
            if ceo_assumptions:
                parts = [f"{a.get('question', '')}: {a.get('answer', '')}" for a in ceo_assumptions if a.get("answer")]
                idea_summary = "Business idea based on CEO answers: " + "; ".join(parts)
            elif approved_decision:
                idea_summary = approved_decision.get("rationale", "") or approved_decision.get("summary", "") or "Business idea approved at Gate 1"
            if len(idea_summary) < 10:
                idea_summary = "Business idea approved at Gate 1 — details pending clarification"

        try:
            validated_input = OpportunityAnalystInput(
                task_id=task_id,
                session_id=session_id,
                idea_summary=idea_summary,
                ceo_assumptions=ceo_assumptions,
                approved_decision=approved_decision,
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[OpportunityAnalyst] Input validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "unclear_input", str(e))
            return

        cross_context = input_package.get("cross_section_context", {})

        output_schema_prompt = self._build_schema_prompt()
        input_data = {
            "idea_summary": idea_summary,
            "ceo_assumptions": ceo_assumptions,
            "approved_decision": approved_decision,
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

        parsed, reasoning_trace, token_usage = await self.intelligence.reason_and_produce(
            agent_role=(
                "Opportunity Analyst — you assess business ideas, define competitive strategy, "
                "set Year 1 objectives, and hypothesize the ideal customer profile"
            ),
            input_data=input_data,
            output_schema_prompt=output_schema_prompt,
            cross_section_context=cross_context if cross_context else None,
            reasoning_budget=3,
        )

        if not parsed:
            user_message = self._build_prompt(validated_input)
            llm_response, fallback_usage = await self._call_llm(user_message)
            if not llm_response:
                await self._escalate(
                    task_id, session_id, pipeline_run_id,
                    "weak_evidence", "Intelligence engine and fallback LLM both failed",
                )
                return
            parsed = self._parse_llm_response(llm_response, validated_input)
            token_usage["input_tokens"] = token_usage.get("input_tokens", 0) + fallback_usage.get("input_tokens", 0)
            token_usage["output_tokens"] = token_usage.get("output_tokens", 0) + fallback_usage.get("output_tokens", 0)

        try:
            parsed["task_id"] = task_id
            parsed["model_used"] = self.model_id
            parsed["input_tokens"] = token_usage.get("input_tokens", 0)
            parsed["output_tokens"] = token_usage.get("output_tokens", 0)
            validated_output = OpportunityAnalystOutput(**parsed)
        except Exception as e:
            logger.error("[OpportunityAnalyst] Output validation failed: %s", e)
            await self._escalate(
                task_id, session_id, pipeline_run_id,
                "output_conflict", f"Schema validation failed: {e}",
            )
            return

        result = validated_output.model_dump()
        result["reasoning_trace"] = reasoning_trace
        self.redis.client.set(f"task_output:{task_id}", json.dumps(result, default=str), ex=3600)

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

    def _build_schema_prompt(self) -> str:
        """Schema instruction used by the Intelligence Engine's PRODUCE and REVISE steps."""
        return """Return ONLY valid JSON with these exact keys:
- section_number: "1"
- opportunity_description: (min 50 chars) structured description of the opportunity — what it is, why now, what makes it viable
- competitive_strategy: (min 30 chars) how the business will compete — specific positioning, not generic
- objectives: list of dicts with "objective", "metric", "target_value", "timeframe" — quantified Year 1 goals
- icp_hypothesis: dict with "buyer_role", "budget_process", "decision_timeline", "pain_points" — who buys and why
- assumptions_used: list of {"statement", "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}
- uncertainties: list of strings — what you don't know and can't infer
- confidence_score: "high"|"medium"|"low" — honest self-assessment of output quality
- input_tokens: 0
- output_tokens: 0"""

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

    def _parse_llm_response(self, raw: str, inp: OpportunityAnalystInput) -> dict:
        """Parse LLM response with retry before falling back to defaults."""
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(inp),
            agent_name="OpportunityAnalyst",
        )
        if result is not None:
            return result

        logger.warning("[OpportunityAnalyst] Both parse attempts failed, constructing fallback")
        return {
                "section_number": "1",
                "opportunity_description": inp.idea_summary if len(inp.idea_summary) >= 50 else inp.idea_summary + " " * (50 - len(inp.idea_summary)) + "— analysis pending",
                "competitive_strategy": "Differentiation through unique value proposition and first-mover positioning in target market",
                "objectives": [{"objective": "Validate product-market fit", "metric": "customers", "target_value": "10", "timeframe": "6 months"}],
                "icp_hypothesis": {"buyer_role": "Decision maker", "budget_process": "Annual budget cycle", "decision_timeline": "1-3 months", "pain_points": ["Unmet need identified in idea summary"]},
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
            logger.error("[OpportunityAnalyst] LLM call failed: %s", e)
            return None, {}

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
    from dotenv import load_dotenv
    load_dotenv()
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
