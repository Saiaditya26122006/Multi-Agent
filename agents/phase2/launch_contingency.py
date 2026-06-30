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
from agents.phase2.llm_utils import parse_json_with_retry, signal_ready
from agents.phase2.intelligence_engine import IntelligenceEngine
from schemas.inputs.launch_contingency import LaunchContingencyInput
from schemas.outputs.launch_contingency import LaunchContingencyOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Launch & Contingency agent in a multi-agent business plan system.
Your role: build the start-up programme with milestones that account for real dependencies, and
contingency plans that directly address the specific kill conditions identified in prior sections.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. DEPENDENCY-AWARE SEQUENCING (Milestones must respect reality)
   - Every milestone must list its dependencies explicitly. "Launch product" cannot come before "Complete MVP" which cannot come before "Hire engineer" (from Section 4 headcount_plan).
   - Cross-reference headcount_plan from Section 4: if a milestone requires a person who has not been hired yet, the milestone date must be AFTER their start date + ramp time (assume 3 months to productivity).
   - Cross-reference revenue_assumptions from Section 8: if "first customer" is a milestone, the timeline must account for the sales_cycle_months. A 6-month sales cycle means first customer cannot be Month 2.
   - If dependencies create a critical path longer than available runway, flag this as a FATAL conflict.

2. CONTINGENCY TIED TO KILL CONDITIONS (Not generic risk management)
   - Each contingency_scenario must address a specific risk identified in earlier sections:
     * primary_risk_factor from Section 12 (financial simulation)
     * High-severity threats from Section 5 (SWOT)
     * Capability gaps rated "high" from Section 4 (org design)
   - For each scenario, the "trigger" must be a measurable condition (e.g., "Revenue < $X by month Y" or "Churn > 15% for 2 consecutive months"), not a vague description.
   - The "response" must be a specific action with a timeline, not "pivot" or "reassess." What pivot? To what? In how long?

3. CAPITAL PLAN (Specific amounts tied to milestones)
   - Capital needs must trace to: burn_rate (from Section 12) x months_to_break_even + buffer (minimum 3 months).
   - State the specific funding round type (bootstrapped, pre-seed, seed, series A) and typical range for that type.
   - Tie funding milestones to business milestones: "Raise seed at $X after achieving [milestone Y] which de-risks [assumption Z]."
   - If the business plan shows no path to profitability within available capital, this must be stated bluntly.

4. EXIT CONDITIONS (Specific triggers, not feelings)
   - Exit conditions must be quantitative: "Wind down if [metric] < [threshold] by [date]."
   - Multiple triggers should be defined: cash runway trigger (< 3 months with no funding path), traction trigger (zero customers by month X), and market trigger (key market assumption disproved).
   - Exit conditions are not failures — they are rational decision points that protect the founder's time and remaining capital.

5. CRITICAL PATH IDENTIFICATION
   - The critical_path_item must be the single thing that, if delayed, delays EVERYTHING downstream.
   - It must connect to the longest dependency chain in the launch_programme.
   - State what happens if the critical path item is delayed by 3 months. What is the cascading impact?

## ANTI-PATTERNS — If you catch yourself writing any of these, you do not have enough information:
- NEVER write "build MVP" as a milestone without specifying scope, who builds it, and definition of done.
- NEVER write "achieve product-market fit" as a milestone — that is an outcome, not a milestone. What measurable signals indicate PMF? (e.g., "5 paying customers with <10% monthly churn")
- NEVER write "secure funding" without specifying: how much, from whom (type of investor), and what milestone makes you fundable.
- NEVER write a contingency plan that says "pivot to a different market" without specifying which market and why it is more viable.
- NEVER set milestones at even intervals (Month 3, Month 6, Month 9) unless the dependencies actually support that spacing.
- NEVER write exit_conditions as "if the business is not working" — define exactly what "not working" means in numbers.

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If break_even_analysis is empty AND probability_distribution is empty AND there is no primary_risk_factor, flag as FATAL: "Cannot build contingency plan without financial model outputs from Section 12."
- If the financial model shows >60% probability of cash-out AND there is no identified funding path, flag as FATAL: "Launch plan is not viable without addressing the >60% failure probability from simulation."

## OUTPUT LENGTH CONSTRAINTS — Obey these limits strictly:
- launch_programme: MAXIMUM 6 milestones. Pick the 6 most critical. Do NOT list every sub-task.
- prerequisite_conditions: MAXIMUM 4 items.
- contingency_scenarios: MAXIMUM 3 scenarios. Only the highest-impact scenarios.
- assumptions_used: MAXIMUM 5 assumptions. Only those that materially affect the plan.
- uncertainties: MAXIMUM 4 items.
- capital_plan: 2-3 sentences max.
- critical_path_item: 1-2 sentences max.
- exit_conditions: 2-3 sentences covering the key triggers.
- All string fields: MAXIMUM 200 characters each. Be concise.
- Total output must fit comfortably in one JSON response. Prefer brevity over exhaustiveness.

## Rules:
- launch_programme must have 3-6 milestones with target_date_months, responsible, success_metric, dependencies
- prerequisite_conditions must have 2-4 items — things that must be TRUE before launch starts
- capital_plan must be at least 50 characters explaining how and when to raise specific amounts
- critical_path_item must identify the single most important thing to get right first and why
- contingency_scenarios must be included when probability_distribution shows >30% cash-out rate
- exit_conditions must define when to wind down — specific quantitative triggers
- Milestones must be sequenced logically with explicit dependencies

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, launch_programme, prerequisite_conditions, capital_plan, critical_path_item, contingency_scenarios, exit_conditions, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
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


class LaunchContingencyAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"))
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)

    async def setup(self):
        logger.info("[LaunchContingency] Starting")
        self.add_behaviour(ListenBehaviour())
        signal_ready(self.redis, "launch_contingency")

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
            validated_input = LaunchContingencyInput(
                task_id=task_id,
                session_id=session_id,
                revenue_assumptions=input_package.get("revenue_assumptions", {}),
                headcount_plan=input_package.get("headcount_plan", {}),
                break_even_analysis=input_package.get("break_even_analysis", {}),
                probability_distribution=input_package.get("probability_distribution", []),
                primary_risk_factor=input_package.get("primary_risk_factor", ""),
                market_entry_strategy=input_package.get("market_entry_strategy", ""),
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[LaunchContingency] Input validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "unclear_input", str(e))
            return

        rag_context = rag_enrich(
            "launch plan contingency risks milestones compliance legal",
            section="13",
        )
        if rag_context:
            input_package["rag_launch_context"] = rag_context

        cross_context = input_package.get("cross_section_context", {})
        learning_context = input_package.get("learning_context", "")

        revision_required = input_package.get("revision_required", False)
        revision_feedback = input_package.get("revision_feedback", "")
        if revision_required and revision_feedback:
            learning_context += f"\n\nMANDATORY REVISIONS (from quality review):\n{revision_feedback}\nFix these issues. Do NOT weaken your analysis — make it more rigorous."

        input_data = {
            "revenue_assumptions": input_package.get("revenue_assumptions", {}),
            "headcount_plan": input_package.get("headcount_plan", {}),
            "break_even_analysis": input_package.get("break_even_analysis", {}),
            "probability_distribution": input_package.get("probability_distribution", []),
            "primary_risk_factor": input_package.get("primary_risk_factor", ""),
            "market_entry_strategy": input_package.get("market_entry_strategy", ""),
        }

        parsed, reasoning_trace, token_usage = await self.intelligence.reason_and_produce(
            agent_role=(
                "Launch & Contingency — you build the start-up programme with sequenced milestones, "
                "capital plan, critical path, contingency scenarios, and exit conditions"
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
            validated_output = LaunchContingencyOutput(**parsed)
        except Exception as e:
            logger.error("[LaunchContingency] Output validation failed: %s", e)
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
- section_number: "13"
- launch_programme: list of {"milestone": str, "target_date_months": int, "responsible": str, "success_metric": str, "dependencies": [str]} (min 3, logically sequenced)
- prerequisite_conditions: [str] (min 2 — what must happen before launch)
- capital_plan: str (min 50 chars — how and when to raise capital, specific amounts)
- critical_path_item: str (the single most important thing to get right first)
- contingency_scenarios: list of {"scenario": str, "trigger": str, "response": str}|null
- exit_conditions: str|null (when to wind down — specific triggers, not vague)
- assumptions_used: list of {"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0"""

    def _build_prompt(self, inp: LaunchContingencyInput) -> str:
        return f"""Build the start-up programme and contingency plan.

REVENUE ASSUMPTIONS: {json.dumps(inp.revenue_assumptions, indent=2)}
HEADCOUNT PLAN: {json.dumps(inp.headcount_plan, indent=2)}
BREAK-EVEN ANALYSIS: {json.dumps(inp.break_even_analysis, indent=2)}
SIMULATION RESULTS (P10/P50/P90): {json.dumps(inp.probability_distribution, indent=2)}
PRIMARY RISK FACTOR: {inp.primary_risk_factor}
MARKET ENTRY STRATEGY: {inp.market_entry_strategy}

Return ONLY valid JSON with these exact keys:
- section_number: "13"
- launch_programme: list of {{"milestone": str, "target_date_months": int, "responsible": str, "success_metric": str, "dependencies": [str]}} (min 3)
- prerequisite_conditions: [str] (min 2, what must happen before launch)
- capital_plan: str (min 50 chars, how and when to raise capital)
- critical_path_item: str (the single most important thing to get right first)
- contingency_scenarios: list of {{"scenario": str, "trigger": str, "response": str}}|null
- exit_conditions: str|null (when to wind down if assumptions fail)
- assumptions_used: list of {{"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0
"""

    def _parse_llm_response(self, raw: str, inp: LaunchContingencyInput) -> dict:
        """Parse LLM response with retry before falling back to defaults."""
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(inp),
            agent_name="LaunchContingency",
        )
        if result is not None:
            return result

        logger.warning("[LaunchContingency] Both parse attempts failed, constructing fallback")
        return {
                "section_number": "13",
                "launch_programme": [
                    {"milestone": "Complete MVP development", "target_date_months": 2, "responsible": "Founder", "success_metric": "Working prototype", "dependencies": []},
                    {"milestone": "First customer acquisition", "target_date_months": 4, "responsible": "Founder", "success_metric": "Signed contract or first payment", "dependencies": ["Complete MVP development"]},
                    {"milestone": "Achieve product-market fit signal", "target_date_months": 6, "responsible": "Team", "success_metric": "3+ paying customers with positive feedback", "dependencies": ["First customer acquisition"]},
                ],
                "prerequisite_conditions": ["Secure initial funding or bootstrap runway", "Validate core value proposition with target customers"],
                "capital_plan": "Bootstrap initial development with founder capital. Seek pre-seed funding at MVP stage to extend runway to 18 months.",
                "critical_path_item": "Validating that target customers will pay for the solution before investing in full build-out",
                "contingency_scenarios": [{"scenario": "No customers by month 6", "trigger": "Zero revenue at 6 months", "response": "Pivot positioning or target segment"}],
                "exit_conditions": "Wind down if cash runway drops below 3 months with no revenue traction",
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
            logger.error("[LaunchContingency] LLM call failed: %s", e)
            return None, {}

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "13", "gap_key": "budget_constraints"})
        await self._send_msg(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "section_number": "13"})
        await self._send_msg(msg)


async def main():
    from dotenv import load_dotenv
    load_dotenv()
    jid = os.getenv("LAUNCH_CONTINGENCY_JID")
    password = os.getenv("LAUNCH_CONTINGENCY_PASSWORD")
    if not jid or not password:
        raise ValueError("LAUNCH_CONTINGENCY_JID and PASSWORD must be set")
    agent = LaunchContingencyAgent(jid=jid, password=password)
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
