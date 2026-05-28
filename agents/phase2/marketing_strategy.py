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

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, target_market_analysis, competitors, competitive_advantages, marketing_mix, customer_relations, revenue_assumptions, cac_assumptions, market_entry_strategy, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
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
        elif performative == "revise":
            await self.agent.handle_revise(task_id, session_id, pipeline_run_id, content)
        elif performative == "propose":
            await self.agent.handle_propose(task_id, session_id, pipeline_run_id, sender, content)


class MarketingStrategyAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"))
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)

    async def setup(self):
        logger.info("[MarketingStrategy] Starting")
        self.add_behaviour(ListenBehaviour())
        signal_ready(self.redis, "marketing_strategy")

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

        cross_context = input_package.get("cross_section_context", {})
        learning_context = input_package.get("learning_context", "")

        revision_required = input_package.get("revision_required", False)
        revision_feedback = input_package.get("revision_feedback", "")
        if revision_required and revision_feedback:
            learning_context += f"\n\nMANDATORY REVISIONS (from quality review):\n{revision_feedback}\nFix these issues. Do NOT weaken your analysis — make it more rigorous."

        input_data = {
            "swot_matrix": input_package.get("swot_matrix", {}),
            "icp_hypothesis": input_package.get("icp_hypothesis", {}),
            "competitive_strategy": input_package.get("competitive_strategy", ""),
            "market_context": input_package.get("market_context", ""),
            "strategic_implications": input_package.get("strategic_implications", ""),
            "pricing_assumption": input_package.get("pricing_assumption"),
            "target_volume": input_package.get("target_volume"),
            "cac_assumptions": input_package.get("cac_assumptions"),
        }

        parsed, reasoning_trace, token_usage = await self.intelligence.reason_and_produce(
            agent_role=(
                "Marketing Strategy — you build the full marketing plan including target market, "
                "competitive positioning, marketing mix, revenue assumptions, and CAC estimates"
            ),
            input_data=input_data,
            output_schema_prompt=self._build_schema_prompt(),
            cross_section_context=cross_context if cross_context else None,
            reasoning_budget=4 if revision_required else 3,
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
            validated_output = MarketingStrategyOutput(**parsed)
        except Exception as e:
            logger.error("[MarketingStrategy] Output validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "output_conflict", str(e))
            return

        result = validated_output.model_dump()
        result["reasoning_trace"] = reasoning_trace
        await self._send_inform(task_id, session_id, pipeline_run_id, result)

    async def handle_revise(self, task_id, session_id, pipeline_run_id, content):
        """Handle revision request from Council Agent."""
        revision_instructions = content.get("revision_instructions", "")
        original_output = content.get("original_output", {})
        persona_critiques = content.get("persona_critiques", [])

        critique_text = "\n".join(
            f"- [{c.get('persona', '')}] {c.get('top_finding', '')}"
            for c in persona_critiques if c.get("severity") in ("critical", "minor")
        )

        input_package = {
            "icp_hypothesis": original_output.get("icp_hypothesis", ""),
            "competitive_strategy": original_output.get("competitive_strategy", ""),
            "revenue_assumptions": original_output.get("revenue_assumptions", {}),
            "revision_required": True,
            "revision_feedback": f"COUNCIL REVIEW FEEDBACK:\n{revision_instructions}\n\nSPECIFIC CRITIQUES:\n{critique_text}",
            "cross_section_context": content.get("cross_section_context", {}),
        }

        revised_content = {"task": {"input_package": input_package, "task_id": task_id}}
        await self.handle_request(task_id, session_id, pipeline_run_id, revised_content)

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

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON with these exact keys:
- section_number: "8"
- target_market_analysis: {"segmentation": str, "icp_refined": str, "market_size_tam_sam_som": str}
- competitors: list of {"name": str, "positioning": str, "pricing": str|null, "strengths": [str], "weaknesses": [str]} (min 2)
- competitive_advantages: [str] (min 2, specific not generic)
- marketing_mix: {"product": str, "pricing_policy": str, "distribution": str, "promotion": str}
- customer_relations: {"communication": str, "loyalty_strategy": str}
- revenue_assumptions: {"price_per_unit": float, "volume_year1": int, "volume_year2": int, "volume_year3": int, "sales_cycle_months": int}
- cac_assumptions: {"cac_estimate": float, "cac_source": str, "confidence": "high"|"medium"|"low"}
- market_entry_strategy: str (min 50 chars — specific go-to-market plan)
- assumptions_used: list of {"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0"""

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

    def _parse_llm_response(self, raw: str, inp: MarketingStrategyInput) -> dict:
        """Parse LLM response with retry before falling back to defaults."""
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(inp),
            agent_name="MarketingStrategy",
            max_tokens=8192,
        )
        if result is not None:
            return result

        logger.warning("[MarketingStrategy] Both parse attempts failed, constructing fallback")
        return {
                "section_number": "8",
                "target_market_analysis": {"segmentation": "To be determined based on ICP validation", "icp_refined": "Initial hypothesis requires market testing", "market_size_tam_sam_som": "Requires further research"},
                "competitors": [
                    {"name": "Incumbent Solution A", "positioning": "Established market player", "pricing": None, "strengths": ["Brand recognition", "Existing customer base"], "weaknesses": ["Slow innovation", "Legacy technology"]},
                    {"name": "Alternative/Substitute B", "positioning": "Adjacent market solution", "pricing": None, "strengths": ["Low cost"], "weaknesses": ["Poor fit for target use case"]},
                ],
                "competitive_advantages": ["Novel approach to customer problem", "Speed and agility as early-stage venture"],
                "marketing_mix": {"product": "Core product addressing identified pain points", "pricing_policy": "Value-based pricing aligned with market", "distribution": "Direct-to-customer digital channels", "promotion": "Content marketing and targeted outreach"},
                "customer_relations": {"communication": "Direct engagement via digital channels", "loyalty_strategy": "Early adopter program with feedback loop"},
                "revenue_assumptions": {"price_per_unit": 100.0, "volume_year1": 100, "volume_year2": 500, "volume_year3": 1500, "sales_cycle_months": 3},
                "cac_assumptions": {"cac_estimate": 500.0, "cac_source": "Industry benchmark — not validated", "confidence": "low"},
                "market_entry_strategy": "Focus on early adopter segment with direct sales approach, then expand through referrals and content marketing",
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
            logger.error("[MarketingStrategy] LLM call failed: %s", e)
            return None, {}

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
        council_jid = os.getenv("COUNCIL_AGENT_JID", "")
        target_jid = council_jid if council_jid else os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=target_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({
            "output": output,
            "section_number": "8",
            "agent_name": "marketing_strategy",
        })
        await self._send_msg(msg)


async def main():
    from dotenv import load_dotenv
    load_dotenv()
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
