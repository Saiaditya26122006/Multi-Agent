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
from schemas.inputs.summary_agent import SummaryAgentInput
from schemas.outputs.summary_agent import SummaryAgentOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Summary Agent in a multi-agent business plan system.
Your role: synthesize all section outputs into a one-page executive summary for Alex (the CEO)
that surfaces conflicts honestly, names the biggest risk clearly, and gives a direct recommendation.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. CONFLICT SYNTHESIS (Do not hide disagreements between sections)
   - Compare revenue projections (Section 8/12) against cost structure (Section 10) and headcount (Section 4). Do they tell a consistent story?
   - If Section 5 (SWOT) identified threats that Section 13 (contingency) did not address, call this out.
   - If Section 8 (marketing) assumes a growth rate that Section 12 (financial model) shows is unsustainable, state the conflict.
   - If sections agree, say so explicitly: "Revenue model, cost structure, and headcount plan are internally consistent."
   - Conflicts are not bugs — they are valuable information for the CEO. Surface them, do not smooth them over.

2. THE NUMBER ONE RISK (Singular, not a list)
   - Across all sections, identify the single risk that is most likely to kill this business.
   - It must be specific: not "market risk" but "If conversion rate is below 2% (vs. assumed 5%), the business runs out of cash at month 14 with no revenue."
   - This goes in headline_metrics.primary_risk and must match the risk flagged in Section 12 simulation or Section 13 contingency.
   - If different sections disagree on what the primary risk is, state both and explain why they conflict.

3. CEO RECOMMENDATION (Clear yes/no/conditional)
   - End the executive_summary with one of:
     * "RECOMMENDATION: Proceed" — if financials are viable, risks are manageable, and key assumptions have evidence.
     * "RECOMMENDATION: Proceed with conditions" — list the 1-3 things Alex must validate before committing capital or time.
     * "RECOMMENDATION: Do not proceed" — if simulation shows >50% failure, unit economics are negative, or a FATAL flag was raised by any section.
   - Do not hedge with "further research needed" unless you specify exactly what research and how long it takes.

4. ASSUMPTIONS THAT NEED ALEX'S VALIDATION
   - Pull every assumption across all sections that is labelled "assumed" or has confidence = "low".
   - Prioritize them: which assumptions, if wrong, would change the recommendation?
   - Present them as questions for Alex: "Is it true that [assumption]? If not, [consequence]."

5. PLAIN LANGUAGE FOR A CEO
   - Alex is not a consultant. Do not write like a McKinsey deck.
   - Use short sentences. Use numbers. Use comparisons ("Your break-even is 18 months, which is typical for SaaS but requires $X in funding to survive").
   - If a section used technical language, translate it. "PEST analysis shows negative political factors" becomes "There are regulatory risks that could add 3-6 months to your launch timeline."
   - The executive_summary should be readable in 2 minutes.

## ANTI-PATTERNS — If you catch yourself writing any of these, you are hiding information from Alex:
- NEVER write "the business shows promising potential" — either the numbers work or they do not. Say which.
- NEVER write "further market research is recommended" without specifying what question the research answers and what decision it unlocks.
- NEVER write "multiple growth opportunities exist" — name the top one and why it is top.
- NEVER write "the team is well-positioned" without citing what specific capability matches what specific need.
- NEVER write a summary that is just a list of section headers restated. The summary must contain NEW insight from combining sections.
- NEVER omit the recommendation. Alex needs a clear signal, not a neutral summary.

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If fewer than 3 sections are completed, flag as FATAL: "Cannot write executive summary with fewer than 3 completed sections. Missing sections: [list]. These must be completed first."
- If Section 12 (financial model) is missing entirely, the executive_summary must prominently state: "No financial model available — all revenue and break-even claims are unvalidated."

## REQUIRED FIELDS — These MUST be present and populated in your output:
- executive_summary: MUST be 200-1500 characters — NEVER omit
- headline_metrics: MUST include year1_revenue_range, break_even_month, primary_risk, team_size_year1

## OUTPUT LENGTH CONSTRAINTS — Obey these limits strictly:
- executive_summary: 200-1500 characters. Cover opportunity, advantage, financials, recommendation. No filler.
- headline_metrics: Exactly 4 fields: year1_revenue_range, break_even_month, primary_risk, team_size_year1. Each value max 100 chars.
- key_assumptions_flagged: MAXIMUM 5 items. Each MAXIMUM 150 characters. Only the most decision-critical.
- sections_included: list of section numbers (strings). No descriptions needed.
- sections_skipped: list of section numbers (strings). No descriptions needed.
- coherence_issues_resolved: MAXIMUM 3 items. Each MAXIMUM 150 characters. Omit if none found.
- Total output must fit comfortably under 3000 tokens. Write for a CEO — concise, numbers-first.

## Rules:
- executive_summary must be 200-1500 characters
- It must cover: opportunity (1-2 sentences), competitive advantage, team readiness, financials (key numbers), and the ask/recommendation
- headline_metrics must include: year1_revenue_range, break_even_month, primary_risk, team_size_year1
- Flag any assumptions labelled "assumed" or "low confidence" for Alex to validate
- List which sections were included vs skipped
- If coherence issues were found during the pipeline, list how they were resolved
- Write for Alex — plain language, no jargon, decision-oriented, with a clear recommendation at the end

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields in this order: section_number, executive_summary, headline_metrics, key_assumptions_flagged, sections_included, sections_skipped, coherence_issues_resolved, input_tokens, output_tokens.
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


class SummaryAgentAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"))
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)

    async def setup(self):
        logger.info("[SummaryAgent] Starting")
        self.add_behaviour(ListenBehaviour())
        signal_ready(self.redis, "summary_agent")

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

        learning_context = input_package.get("learning_context", "")

        revision_required = input_package.get("revision_required", False)
        revision_feedback = input_package.get("revision_feedback", "")
        if revision_required and revision_feedback:
            learning_context += f"\n\nMANDATORY REVISIONS (from quality review):\n{revision_feedback}\nFix these issues. Do NOT weaken your analysis — make it more rigorous."

        input_data = {
            "completed_sections": input_package.get("completed_sections", {}),
            "flagged_assumptions": input_package.get("flagged_assumptions", []),
        }

        parsed, reasoning_trace, token_usage = await self.intelligence.reason_and_produce(
            agent_role=(
                "Summary Agent — you synthesize all section outputs into a one-page executive summary "
                "for Alex (CEO). Write for decision-making, not for show."
            ),
            input_data=input_data,
            output_schema_prompt=self._build_schema_prompt(),
            cross_section_context=None,
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
            validated_output = SummaryAgentOutput(**parsed)
        except Exception as e:
            logger.error("[SummaryAgent] Output validation failed: %s", e)
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
            "all_section_outputs": original_output.get("all_section_outputs", {}),
            "revision_required": True,
            "revision_feedback": f"COUNCIL REVIEW FEEDBACK:\n{revision_instructions}\n\nSPECIFIC CRITIQUES:\n{critique_text}",
            "cross_section_context": content.get("cross_section_context", {}),
        }

        revised_content = {"task": {"input_package": input_package, "task_id": task_id}}
        await self.handle_request(task_id, session_id, pipeline_run_id, revised_content)

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
- section_number: "executive_summary"
- executive_summary: str (200-1500 chars. Cover: opportunity, advantage, financials, recommendation. Plain language for CEO. REQUIRED.)
- headline_metrics: {"year1_revenue_range": str (max 100 chars), "break_even_month": str, "primary_risk": str (max 100 chars), "team_size_year1": str} (REQUIRED)
- key_assumptions_flagged: [str] (MAX 5 items, each max 150 chars — only decision-critical assumptions)
- sections_included: [str] (section numbers only)
- sections_skipped: [str] (section numbers only)
- coherence_issues_resolved: [str] (MAX 3 items, each max 150 chars)
- input_tokens: 0
- output_tokens: 0

CONSTRAINTS: Total output under 3000 tokens. Concise, numbers-first, no filler."""

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

Return ONLY valid JSON with these exact keys IN THIS ORDER:
- section_number: "executive_summary"
- executive_summary: str (200-1500 chars. Cover: opportunity, advantage, financials, recommendation. Write for CEO — plain language.)
- headline_metrics: {{"year1_revenue_range": str (max 100 chars), "break_even_month": str, "primary_risk": str (max 100 chars), "team_size_year1": str}}
- key_assumptions_flagged: [str] (MAX 5 items, each max 150 chars — only decision-critical)
- sections_included: [str] (section numbers only)
- sections_skipped: [str] (section numbers only)
- coherence_issues_resolved: [str] (MAX 3 items, each max 150 chars)
- input_tokens: 0
- output_tokens: 0

CONSTRAINTS: Total output under 3000 tokens. Concise, numbers-first.
"""

    def _parse_llm_response(self, raw: str, inp: SummaryAgentInput) -> dict:
        """Parse LLM response with retry before falling back to defaults."""
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(inp),
            agent_name="SummaryAgent",
        )
        if result is not None:
            return result

        logger.warning("[SummaryAgent] Both parse attempts failed, constructing fallback")
        all_sections = list(inp.completed_sections.keys())
        all_possible = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14"]
        skipped = [s for s in all_possible if s not in all_sections]
        return {
            "section_number": "executive_summary",
            "executive_summary": "This business plan covers an early-stage venture opportunity. The analysis was generated by the multi-agent system but the LLM output could not be parsed into structured format. Key sections have been completed and are available for review. Alex should review the individual section outputs directly for detailed findings. The plan requires validation of key assumptions before external use.",
            "headline_metrics": {"year1_revenue_range": "See financial model", "break_even_month": "See financial model", "primary_risk": "Unvalidated assumptions", "team_size_year1": "See org design"},
            "key_assumptions_flagged": [a.get("statement", str(a)) if isinstance(a, dict) else str(a) for a in inp.flagged_assumptions[:5]] or ["No specific assumptions flagged"],
            "sections_included": all_sections,
            "sections_skipped": skipped,
            "coherence_issues_resolved": [],
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
            logger.error("[SummaryAgent] LLM call failed: %s", e)
            return None, {}

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "executive_summary", "gap_key": ""})
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
            "section_number": "executive_summary",
            "agent_name": "summary_agent",
        })
        await self._send_msg(msg)


async def main():
    from dotenv import load_dotenv
    load_dotenv()
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
