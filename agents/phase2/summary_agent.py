"""Summary Agent — synthesizes all section outputs into a one-page executive summary."""

import json
import logging
import os

from agents.phase2.base_child_agent import BaseChildAgent
from agents.phase2.llm_utils import parse_json_with_retry
from agents.phase2.message_bus import ACLMessage
from agents.phase2.rag_mixin import rag_enrich
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

ALL_SECTIONS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14"]


class SummaryAgentAgent(BaseChildAgent):
    SYSTEM_PROMPT = SYSTEM_PROMPT
    AGENT_NAME = "SummaryAgent"
    AGENT_ROLE = (
        "Summary Agent — you synthesize all section outputs into a one-page executive summary "
        "for Alex (CEO). Write for decision-making, not for show."
    )
    SECTION_NUMBER = "executive_summary"
    MODEL_ENV = "CLAUDE_HAIKU_MODEL"
    MODEL_DEFAULT = "claude-haiku-4-5-20251001"
    INPUT_SCHEMA = SummaryAgentInput
    OUTPUT_SCHEMA = SummaryAgentOutput

    def _default_gap_key(self) -> str:
        return ""

    def reasoning_budget(self, revision_required: bool) -> int:
        return 3 if revision_required else 2

    def _rag_enrich(self) -> str:
        return rag_enrich(
            "executive summary product value proposition key decisions",
            section="1",
        )

    def _extract_input(self, input_package: dict, task: dict) -> dict:
        return {
            "completed_sections": input_package.get("completed_sections", {}),
            "flagged_assumptions": input_package.get("flagged_assumptions", []),
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

    def _build_ie_input_data(self, input_package: dict) -> dict:
        return {
            "completed_sections": input_package.get("completed_sections", {}),
            "flagged_assumptions": input_package.get("flagged_assumptions", []),
        }

    async def handle_revise(self, task_id: str, session_id: str, pipeline_run_id: str, content: dict):
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

    async def _send_inform(self, task_id: str, session_id: str, pipeline_run_id: str, output: dict):
        """Summary output goes to the Council Agent (falls back to Mother if no bus receiver)."""
        content = {"output": output, "section_number": self.SECTION_NUMBER, "agent_name": "summary_agent"}
        if self._bus is not None:
            msg = ACLMessage(
                sender="summary_agent",
                receiver="council_agent",
                performative="inform",
                content=content,
                task_id=task_id,
                session_id=session_id,
                pipeline_run_id=pipeline_run_id,
            )
            await self._bus.send(msg)
            return
        logger.error("[SummaryAgent] No message bus — cannot send inform")

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

    def _build_prompt(self, validated_input: SummaryAgentInput) -> str:
        all_sections = list(validated_input.completed_sections.keys())
        skipped = [s for s in ALL_SECTIONS if s not in all_sections]

        return f"""Write an executive summary for this business plan.

COMPLETED SECTIONS ({len(all_sections)} total):
{json.dumps(validated_input.completed_sections, indent=2)}

FLAGGED ASSUMPTIONS (low confidence or assumed):
{json.dumps(validated_input.flagged_assumptions, indent=2)}

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

    def _parse_llm_response(self, raw: str, validated_input: SummaryAgentInput) -> dict:
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(validated_input),
            agent_name="SummaryAgent",
        )
        if result is not None:
            return result

        logger.warning("[SummaryAgent] Both parse attempts failed, using fallback")
        return self._fallback_defaults(validated_input)

    def _fallback_defaults(self, validated_input: SummaryAgentInput) -> dict:
        all_sections = list(validated_input.completed_sections.keys())
        skipped = [s for s in ALL_SECTIONS if s not in all_sections]
        return {
            "section_number": "executive_summary",
            "executive_summary": "This business plan covers an early-stage venture opportunity. The analysis was generated by the multi-agent system but the LLM output could not be parsed into structured format. Key sections have been completed and are available for review. Alex should review the individual section outputs directly for detailed findings. The plan requires validation of key assumptions before external use.",
            "headline_metrics": {"year1_revenue_range": "See financial model", "break_even_month": "See financial model", "primary_risk": "Unvalidated assumptions", "team_size_year1": "See org design"},
            "key_assumptions_flagged": [a.get("statement", str(a)) if isinstance(a, dict) else str(a) for a in validated_input.flagged_assumptions[:5]] or ["No specific assumptions flagged"],
            "sections_included": all_sections,
            "sections_skipped": skipped,
            "coherence_issues_resolved": [],
            "input_tokens": 0,
            "output_tokens": 0,
        }
