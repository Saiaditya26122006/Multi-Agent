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
from schemas.inputs.organisation_designer import OrganisationDesignerInput
from schemas.outputs.organisation_designer import OrganisationDesignerOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Organisation Designer agent in a multi-agent business plan system.
Your role: design company structure that directly serves the revenue plan — every role must justify
its existence through contribution to revenue milestones or risk mitigation.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. ROLES TIED TO REVENUE MILESTONES
   - For every role you recommend, answer: "What revenue milestone does this role unlock or accelerate?"
   - If a role does not map to a specific milestone (e.g., "reach first 10 customers", "hit $50K MRR"), it should not be hired in Year 1.
   - Sequence hires by revenue dependency: which role must exist BEFORE revenue can flow?

2. COST JUSTIFICATION (No hire without math)
   - Every hire must pass this test: (expected revenue contribution or cost saved) > (fully loaded annual cost x 1.5).
   - If the business cannot afford the hire at current burn rate, the hire_timeline must reflect when cash flow supports it.
   - For outsource decisions: show the break-even point where hiring becomes cheaper than outsourcing.
   - Headcount costs must include: salary, benefits (add 25-35%), equipment, and ramp time (assume 3 months to full productivity).

3. FOUNDER CAPABILITY GAP ANALYSIS
   - Identify what the founder CAN do vs. what the business NEEDS done in Year 1.
   - Gaps between founder skills and business needs = actual risks, not just "nice to have" hires.
   - Be blunt: if the founder lacks a critical skill (e.g., technical founder with no sales ability), that gap is severity: "high" and must be resolved before revenue milestones.
   - If founder_profile is not provided, this entire analysis is speculative — say so and set confidence_score to "low".

4. STRUCTURE MATCHES STAGE
   - Pre-revenue: Max 3-5 people. Flat structure. No middle management. No "VP of" titles.
   - Post-PMF: Start adding specialization. First hires should be revenue-generating (sales, customer success) or product-building (engineering).
   - Never recommend a COO, CFO, or CHRO for a pre-revenue startup unless the founder explicitly needs one for a regulatory reason.

5. KNOWLEDGE GAPS AS RISKS
   - Knowledge gaps are not just "things we do not know" — they are risks with probability and impact.
   - For each knowledge gap, state: what bad outcome happens if this gap is not filled?

## ANTI-PATTERNS — If you catch yourself writing any of these, you do not have enough information:
- NEVER write "cross-functional team" without specifying which functions and why they must interact.
- NEVER recommend "hire a CTO" for a pre-revenue startup without explaining what technical work requires senior leadership vs. an IC engineer.
- NEVER write "competitive compensation package" — name a salary range appropriate to the market and stage.
- NEVER write "culture of innovation" — describe the actual working practices (async communication, weekly demos, etc.).
- NEVER recommend more than 5 Year 1 hires for a pre-revenue startup unless the founder has explicitly confirmed funding.

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If opportunity_description is missing AND business_type is missing, flag as FATAL: "Cannot design an organisation without knowing what the business does."
- If there is zero information about the founder (no profile, no assumptions, no context), flag the entire capability_gaps section as "speculative — no founder data available."

## REQUIRED FIELDS — These MUST be present and populated in your output:
- confidence_score: MUST be "high", "medium", or "low" — NEVER omit this field
- capability_gaps: MUST have 2-5 items with gap, severity, resolution
- roles_and_responsibilities: MUST have 2-6 roles

## OUTPUT LENGTH CONSTRAINTS — Obey these limits strictly:
- roles_and_responsibilities: MAXIMUM 6 roles. Each role: {title, responsibilities (max 3), required_skills (max 3), hire_timeline, assigned_to}.
- capability_gaps: MAXIMUM 5 items. Each: {gap, severity, resolution} — gap string max 100 chars.
- knowledge_gaps: MAXIMUM 4 items. Each MAXIMUM 100 characters.
- assumptions_used: MAXIMUM 6 items. Each: {statement (max 120 chars), confidence, source, source_detail}.
- uncertainties: MAXIMUM 4 items. Each MAXIMUM 120 characters.
- org_structure: MAXIMUM 200 characters.
- personnel_policy: 50-300 characters. Be concise.
- Total output must fit comfortably under 4000 tokens. Prefer short phrases over paragraphs.

## Rules:
- Each role must have: title, responsibilities, required_skills, hire_timeline, assigned_to
- assigned_to must be one of: founder, hire, outsource, tbd
- headcount_plan must include year_1, year_2, year_3 with cost estimates in dollars
- personnel_policy must be at least 50 characters of substance
- capability_gaps must include gap, severity, and resolution (build/buy/partner)
- If team information is missing, flag as uncertainty — do not assume

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields in this order: section_number, confidence_score, capability_gaps, roles_and_responsibilities, headcount_plan, org_structure, personnel_policy, knowledge_gaps, assumptions_used, uncertainties, input_tokens, output_tokens.
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


class OrganisationDesignerAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"))
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)

    async def setup(self):
        logger.info("[OrganisationDesigner] Starting")
        self.add_behaviour(ListenBehaviour())
        signal_ready(self.redis, "organisation_designer")

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

        opportunity_description = input_package.get("opportunity_description", "")
        if not opportunity_description:
            opportunity_description = input_package.get("idea_summary", "") or "Business opportunity — details from Section 1 pending"

        try:
            validated_input = OrganisationDesignerInput(
                task_id=task_id,
                session_id=session_id,
                opportunity_description=opportunity_description,
                business_type=input_package.get("business_type", "") or "startup",
                founder_profile=input_package.get("founder_profile"),
                team_composition=input_package.get("team_composition"),
                budget_constraints=input_package.get("budget_constraints"),
                strategic_implications=input_package.get("strategic_implications"),
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[OrganisationDesigner] Input validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "unclear_input", str(e))
            return

        cross_context = input_package.get("cross_section_context", {})
        learning_context = input_package.get("learning_context", "")

        revision_required = input_package.get("revision_required", False)
        revision_feedback = input_package.get("revision_feedback", "")
        if revision_required and revision_feedback:
            learning_context += f"\n\nMANDATORY REVISIONS (from quality review):\n{revision_feedback}\nFix these issues. Do NOT weaken your analysis — make it more rigorous."

        input_data = {
            "opportunity_description": opportunity_description,
            "business_type": input_package.get("business_type", "") or "startup",
            "founder_profile": input_package.get("founder_profile"),
            "team_composition": input_package.get("team_composition"),
            "budget_constraints": input_package.get("budget_constraints"),
            "strategic_implications": input_package.get("strategic_implications"),
        }

        parsed, reasoning_trace, token_usage = await self.intelligence.reason_and_produce(
            agent_role=(
                "Organisation Designer — you design company structure, identify capability gaps, "
                "define roles and headcount plan, and create personnel policy"
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
            validated_output = OrganisationDesignerOutput(**parsed)
        except Exception as e:
            logger.error("[OrganisationDesigner] Output validation failed: %s", e)
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
- section_number: "4"
- confidence_score: "high"|"medium"|"low" (REQUIRED — never omit)
- capability_gaps: list of {"gap": str (max 100 chars), "severity": "high"|"medium"|"low", "resolution": "build"|"buy"|"partner"} (2-5 items — REQUIRED)
- roles_and_responsibilities: list of {"title": str, "responsibilities": [str] (max 3), "required_skills": [str] (max 3), "hire_timeline": str, "assigned_to": "founder"|"hire"|"outsource"|"tbd"} (MAX 6 roles)
- headcount_plan: {"year_1": {"count": int, "cost": float}, "year_2": {"count": int, "cost": float}, "year_3": {"count": int, "cost": float}}
- org_structure: str (max 200 chars — flat/functional/matrix, reporting lines)
- personnel_policy: str (50-300 chars — remote/hybrid, equity, culture)
- knowledge_gaps: [str] (MAX 4 items, each max 100 chars)
- assumptions_used: list of {"statement": str (max 120 chars), "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null} (MAX 6 items)
- uncertainties: [str] (MAX 4 items, each max 120 chars)
- input_tokens: 0
- output_tokens: 0

CONSTRAINTS: Total output under 4000 tokens. Short phrases, not paragraphs."""

    def _build_prompt(self, inp: OrganisationDesignerInput) -> str:
        return f"""Design the organisation structure for this business.

OPPORTUNITY: {inp.opportunity_description}
BUSINESS TYPE: {inp.business_type}
FOUNDER PROFILE: {inp.founder_profile or 'Not provided'}
TEAM COMPOSITION: {json.dumps(inp.team_composition) if inp.team_composition else 'Not provided'}
BUDGET CONSTRAINTS: {inp.budget_constraints or 'Not provided'}
STRATEGIC IMPLICATIONS: {inp.strategic_implications or 'Not yet available'}

Return ONLY valid JSON with these exact keys IN THIS ORDER:
- section_number: "4"
- confidence_score: "high"|"medium"|"low" (REQUIRED — never omit)
- capability_gaps: list of {{"gap": str (max 100 chars), "severity": "high"|"medium"|"low", "resolution": "build"|"buy"|"partner"}} (2-5 items)
- roles_and_responsibilities: list of {{"title": str, "responsibilities": [str] (max 3), "required_skills": [str] (max 3), "hire_timeline": str, "assigned_to": "founder"|"hire"|"outsource"|"tbd"}} (MAX 6 roles)
- headcount_plan: {{"year_1": {{"count": int, "cost": float}}, "year_2": {{"count": int, "cost": float}}, "year_3": {{"count": int, "cost": float}}}}
- org_structure: str (max 200 chars)
- personnel_policy: str (50-300 chars)
- knowledge_gaps: [str] (MAX 4 items, each max 100 chars)
- assumptions_used: list of {{"statement": str (max 120 chars), "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}} (MAX 6 items)
- uncertainties: [str] (MAX 4 items, each max 120 chars)
- input_tokens: 0
- output_tokens: 0

CONSTRAINTS: Total output under 4000 tokens. Short phrases, not paragraphs.
"""

    def _parse_llm_response(self, raw: str, inp: OrganisationDesignerInput) -> dict:
        """Parse LLM response with retry before falling back to defaults."""
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(inp),
            agent_name="OrganisationDesigner",
        )
        if result is not None:
            return result

        logger.warning("[OrganisationDesigner] Both parse attempts failed, constructing fallback")
        return {
                "section_number": "4",
                "org_structure": f"Flat startup structure for {inp.business_type or 'early-stage venture'} with founder as CEO and initial team of contractors",
                "capability_gaps": [{"gap": "Technical execution", "severity": "high", "resolution": "hire"}, {"gap": "Market expertise", "severity": "medium", "resolution": "partner"}],
                "roles_and_responsibilities": [{"title": "Founder/CEO", "responsibilities": ["Strategy", "Fundraising", "Product direction"], "required_skills": ["Leadership", "Domain expertise"], "hire_timeline": "Immediate", "assigned_to": "founder"}],
                "headcount_plan": {"year_1": {"count": 3, "cost": 240000.0}, "year_2": {"count": 8, "cost": 640000.0}, "year_3": {"count": 15, "cost": 1200000.0}},
                "personnel_policy": "Remote-first with quarterly in-person offsites. Equity vesting over 4 years with 1-year cliff.",
                "knowledge_gaps": ["Specific team composition not provided", "Budget constraints unknown"],
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
            logger.error("[OrganisationDesigner] LLM call failed: %s", e)
            return None, {}

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "4", "gap_key": "founder_profile"})
        await self._send_msg(msg)

    async def _send_inform(self, task_id, session_id, pipeline_run_id, output):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "inform")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"output": output, "section_number": "4"})
        await self._send_msg(msg)


async def main():
    from dotenv import load_dotenv
    load_dotenv()
    jid = os.getenv("ORGANISATION_DESIGNER_JID")
    password = os.getenv("ORGANISATION_DESIGNER_PASSWORD")
    if not jid or not password:
        raise ValueError("ORGANISATION_DESIGNER_JID and PASSWORD must be set")
    agent = OrganisationDesignerAgent(jid=jid, password=password)
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
