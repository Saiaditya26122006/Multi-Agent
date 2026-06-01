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
from schemas.inputs.operations import OperationsInput
from schemas.outputs.operations import OperationsOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Operations agent in a multi-agent business plan system.
Your role: define how the business actually delivers value — production/delivery process, cost structure
that adds up, capacity plan with real bottleneck analysis, and tooling choices that are justified.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. COST ARITHMETIC (Must add up — no hand-waving)
   - fixed_costs + (variable_costs x volume_year1) + headcount_from_section_4 = total Year 1 operating cost.
   - This total must be cross-referenced against the revenue_assumptions from marketing (Section 8). If costs > revenue for 18+ months, flag the burn rate explicitly.
   - COGS per unit must be derivable: list each component cost that goes into delivering one unit to one customer.
   - Every cost item must have a source_label: "validated" (CEO confirmed), "benchmarked" (industry data), or "assumed" (your estimate). If more than 50% are "assumed," confidence_score must be "low."

2. TOOL AND TECHNOLOGY JUSTIFICATION
   - For every tool or platform recommended, answer: "Why this tool instead of the cheaper/free alternative?"
   - State the monthly/annual cost of each tool.
   - If the business is pre-revenue, default to free/open-source tools unless there is a specific capability gap that requires a paid tool.
   - Never recommend enterprise tools (Salesforce, SAP, etc.) for a team under 10 people unless there is a regulatory or integration requirement.

3. TIMELINE FEASIBILITY (Cross-check with Section 4 headcount)
   - Production milestones must be achievable with the headcount available at that point in time.
   - If the org design says "hire engineer at month 3" but the operations plan requires engineering output at month 1, flag the dependency conflict.
   - For each phase of the production_process, state: who does this work? When are they available? What is their capacity?

4. CAPACITY PLAN (Specific bottlenecks, not generic scaling)
   - At 2x volume: identify the FIRST thing that breaks. Is it a person's time? A tool's limit? A supplier's lead time? Server capacity?
   - At 5x volume: identify what requires fundamental architectural change (new hires, new systems, new suppliers) vs. what scales linearly.
   - State the cost of scaling at each threshold. "Hire more people" is not a capacity plan — "hire 2 additional engineers at $X each to handle Y additional load" is.

5. SUPPLIER AND DEPENDENCY RISK
   - If the business depends on a single supplier or platform (e.g., AWS, Stripe, a specific API), flag the concentration risk.
   - For each critical dependency: what happens if it goes down, raises prices 50%, or discontinues the product?

## ANTI-PATTERNS — If you catch yourself writing any of these, you do not have enough information:
- NEVER write "scalable infrastructure" without naming the specific infrastructure and its scaling mechanism.
- NEVER write "leverage automation to reduce costs" without naming what process is automated, what tool does it, and the cost reduction in dollars.
- NEVER write "agile development process" as an operations strategy — that is a methodology, not a delivery plan.
- NEVER list fixed costs without dollar amounts. "Office space" is not a cost — "$2,000/month for co-working space in [city]" is a cost.
- NEVER write "cloud-based solution" without naming the cloud provider and estimated monthly cost at current scale.

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If there are no revenue_assumptions AND no business_type provided, flag as FATAL: "Cannot design operations without knowing what the business delivers or at what scale."
- If the implied burn rate (fixed_costs + headcount) exceeds any reasonable funding assumption by 3x with no revenue in sight, flag as FATAL: "Operations cost structure is unsustainable without funding information."

## REQUIRED FIELDS — These MUST be present and populated in your output:
- confidence_score: MUST be "high", "medium", or "low" — NEVER omit this field
- production_process: MUST be present (min 50 chars)
- cost_structure: MUST include fixed_costs, variable_costs, cogs_per_unit

## OUTPUT LENGTH CONSTRAINTS — Obey these limits strictly:
- production_process: 50-400 characters. Describe steps concisely — no sub-headings or bullet prose.
- cost_structure: fixed_costs MAXIMUM 6 line items; variable_costs MAXIMUM 4 line items. Each item: {"name": amount}. Include source_labels dict.
- capacity_plan: MAXIMUM 300 characters. State the 2x and 5x bottleneck and solution in one sentence each.
- supplier_strategy: MAXIMUM 200 characters or null.
- rd_plan: MAXIMUM 200 characters or null.
- ip_analysis: MAXIMUM 200 characters or null.
- assumptions_used: MAXIMUM 6 items. Each: {statement (max 120 chars), confidence, source, source_detail}.
- uncertainties: MAXIMUM 4 items. Each MAXIMUM 120 characters.
- Total output must fit comfortably under 4000 tokens. Use numbers and short phrases, not paragraphs.

## Rules:
- production_process must be at least 50 characters describing step-by-step delivery
- cost_structure must include: fixed_costs (with dollar amounts), variable_costs (with dollar amounts), cogs_per_unit — each with source labels
- All cost items must be labelled with source (validated/assumed/benchmarked)
- If technology_description or ip_status are provided, include rd_plan and ip_analysis
- capacity_plan must address scalability — what specifically breaks at 2x and 5x volume
- If this is a pure services business, focus on delivery process rather than manufacturing

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields in this order: section_number, confidence_score, production_process, cost_structure, capacity_plan, supplier_strategy, rd_plan, ip_analysis, assumptions_used, uncertainties, input_tokens, output_tokens.
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


class OperationsAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"))
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)

    async def setup(self):
        logger.info("[Operations] Starting")
        self.add_behaviour(ListenBehaviour())
        signal_ready(self.redis, "operations")

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
            validated_input = OperationsInput(
                task_id=task_id,
                session_id=session_id,
                opportunity_description=input_package.get("opportunity_description", ""),
                business_type=input_package.get("business_type", ""),
                revenue_assumptions=input_package.get("revenue_assumptions", {}),
                swot_matrix=input_package.get("swot_matrix", {}),
                technology_description=input_package.get("technology_description"),
                ip_status=input_package.get("ip_status"),
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[Operations] Input validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "unclear_input", str(e))
            return

        cross_context = input_package.get("cross_section_context", {})
        learning_context = input_package.get("learning_context", "")

        revision_required = input_package.get("revision_required", False)
        revision_feedback = input_package.get("revision_feedback", "")
        if revision_required and revision_feedback:
            learning_context += f"\n\nMANDATORY REVISIONS (from quality review):\n{revision_feedback}\nFix these issues. Do NOT weaken your analysis — make it more rigorous."

        input_data = {
            "opportunity_description": input_package.get("opportunity_description", ""),
            "business_type": input_package.get("business_type", ""),
            "revenue_assumptions": input_package.get("revenue_assumptions", {}),
            "swot_matrix": input_package.get("swot_matrix", {}),
            "technology_description": input_package.get("technology_description"),
            "ip_status": input_package.get("ip_status"),
        }

        parsed, reasoning_trace, token_usage = await self.intelligence.reason_and_produce(
            agent_role=(
                "Operations — you define the production/delivery process, cost structure, "
                "capacity plan, supplier strategy, and optionally R&D and IP analysis"
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
            validated_output = OperationsOutput(**parsed)
        except Exception as e:
            logger.error("[Operations] Output validation failed: %s", e)
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
        return """Return ONLY valid JSON with these exact keys IN THIS ORDER:
- section_number: "10"
- confidence_score: "high"|"medium"|"low" (REQUIRED — never omit)
- production_process: str (50-400 chars — how product/service is delivered, concise steps)
- cost_structure: {"fixed_costs": {"item": amount} (MAX 6 items), "variable_costs": {"item": amount} (MAX 4 items), "cogs_per_unit": float, "source_labels": {"item": "validated"|"assumed"|"benchmarked"}}
- capacity_plan: str (max 300 chars — what breaks at 2x and 5x volume)
- supplier_strategy: str (max 200 chars)|null
- rd_plan: str (max 200 chars)|null (only if tech innovation involved)
- ip_analysis: str (max 200 chars)|null (only if IP relevant)
- assumptions_used: list of {"statement": str (max 120 chars), "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null} (MAX 6 items)
- uncertainties: [str] (MAX 4 items, each max 120 chars)
- input_tokens: 0
- output_tokens: 0

CONSTRAINTS: Total output under 4000 tokens. Numbers and short phrases, not paragraphs."""

    def _build_prompt(self, inp: OperationsInput) -> str:
        tech_section = ""
        if inp.technology_description:
            tech_section = f"\nTECHNOLOGY: {inp.technology_description}\nIP STATUS: {inp.ip_status or 'Not provided'}"

        return f"""Define the operations and production plan for this business.

OPPORTUNITY: {inp.opportunity_description}
BUSINESS TYPE: {inp.business_type}
REVENUE ASSUMPTIONS: {json.dumps(inp.revenue_assumptions, indent=2)}
SWOT: {json.dumps(inp.swot_matrix, indent=2)}{tech_section}

Return ONLY valid JSON with these exact keys IN THIS ORDER:
- section_number: "10"
- confidence_score: "high"|"medium"|"low" (REQUIRED — never omit)
- production_process: str (50-400 chars, concise delivery steps)
- cost_structure: {{"fixed_costs": {{"item": amount}} (MAX 6 items), "variable_costs": {{"item": amount}} (MAX 4 items), "cogs_per_unit": float, "source_labels": {{"item": "validated"|"assumed"|"benchmarked"}}}}
- capacity_plan: str (max 300 chars — 2x and 5x bottlenecks)
- supplier_strategy: str (max 200 chars)|null
- rd_plan: str (max 200 chars)|null
- ip_analysis: str (max 200 chars)|null
- assumptions_used: list of {{"statement": str (max 120 chars), "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}} (MAX 6 items)
- uncertainties: [str] (MAX 4 items, each max 120 chars)
- input_tokens: 0
- output_tokens: 0

CONSTRAINTS: Total output under 4000 tokens. Numbers and short phrases, not paragraphs.
"""

    def _parse_llm_response(self, raw: str, inp: OperationsInput) -> dict:
        """Parse LLM response with retry before falling back to defaults."""
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(inp),
            agent_name="Operations",
        )
        if result is not None:
            return result

        logger.warning("[Operations] Both parse attempts failed, constructing fallback")
        return {
                "section_number": "10",
                "production_process": f"Service delivery process for {inp.business_type or 'this venture'} involving customer acquisition, onboarding, and ongoing delivery",
                "cost_structure": {"fixed_costs": {"office_and_tools": 2000}, "variable_costs": {"delivery_per_unit": 20}, "cogs_per_unit": 20.0, "source_labels": {"office_and_tools": "assumed", "delivery_per_unit": "assumed"}},
                "capacity_plan": "Initial capacity supports Year 1 volume. At 2x volume, hire additional staff. At 5x, invest in automation.",
                "supplier_strategy": None,
                "rd_plan": None,
                "ip_analysis": None,
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
            logger.error("[Operations] LLM call failed: %s", e)
            return None, {}

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "10", "gap_key": "cost_structure"})
        await self._send_msg(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "section_number": "10"})
        await self._send_msg(msg)


async def main():
    from dotenv import load_dotenv
    load_dotenv()
    jid = os.getenv("OPERATIONS_JID")
    password = os.getenv("OPERATIONS_PASSWORD")
    if not jid or not password:
        raise ValueError("OPERATIONS_JID and PASSWORD must be set")
    agent = OperationsAgent(jid=jid, password=password)
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
