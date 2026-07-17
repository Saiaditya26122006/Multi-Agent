"""SWOT Synthesizer Agent — combines PEST, Five Forces, and org capabilities into a SWOT matrix."""

import json
import logging

from agents.phase2.base_child_agent import BaseChildAgent
from agents.phase2.llm_utils import parse_json_with_retry
from agents.phase2.message_bus import ACLMessage
from agents.phase2.rag_mixin import rag_enrich
from schemas.inputs.swot_synthesizer import SWOTSynthesizerInput
from schemas.outputs.swot_synthesizer import SWOTSynthesizerOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the SWOT Synthesizer agent in a multi-agent business plan system.
Your role: synthesize external environment analysis (PEST, Five Forces) with internal organisational
analysis (capabilities, gaps) into a SWOT matrix where every item traces to a specific prior section
and contradictions are surfaced, not hidden.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. TRACEABILITY (Every item must cite its source)
   - Every Strength must trace to a specific capability from Section 4 (org design) or a validated CEO assumption.
   - Every Weakness must trace to a capability_gap from Section 4, an unfilled role, or a low-confidence assumption.
   - Every Opportunity must trace to a PEST factor or Five Forces gap from Section 3.
   - Every Threat must trace to a Five Forces pressure, a PEST risk, or a competitive dynamic from Section 3.
   - The "evidence" field must name the source section and the specific data point. Example: "Section 3: Five Forces — buyer power rated high due to 3 alternative suppliers."
   - If you cannot trace an item to a specific prior section output, it is speculation — label it accordingly.

2. CONTRADICTION DETECTION (Sections that disagree = weaknesses)
   - Compare Section 1 (opportunity) claims against Section 3 (environment) findings. If Section 1 claims "large market" but Section 3 shows high rivalry and low barriers, that is a contradiction — surface it as a weakness.
   - If capability_gaps from Section 4 directly conflict with the competitive_strategy from Section 1, this is a critical weakness.
   - If no contradictions are found, explicitly state "No inter-section contradictions detected" in strategic_implications. Do not just omit them silently.

3. IMPACT SCORING (Not everything is "high")
   - "high" impact means: this factor alone could determine success or failure of the business within 12 months.
   - "medium" impact means: this factor will significantly affect performance but is manageable.
   - "low" impact means: this factor exists but can be deprioritized for now.
   - If you rate more than 50% of items as "high," you are not discriminating — reprioritize.

4. STRATEGIC IMPLICATIONS (Must be actionable, not descriptive)
   - Strategic implications must answer: "Given this SWOT, what should the CEO DO in the next 90 days?"
   - Format: "[Because X strength + Y opportunity], the business should [specific action] within [timeframe]."
   - At least one implication must address the highest-rated threat.

5. PRIORITY STRATEGIC ISSUES (Decision-forcing)
   - These are not summaries — they are decisions the CEO must make.
   - Format: "Decide whether to [option A] or [option B] by [deadline], because [consequence of delay]."

## ANTI-PATTERNS — If you catch yourself writing any of these, you do not have enough information:
- NEVER write "strong market position" without citing what creates the strength (IP? brand? network effects?).
- NEVER write "competitive pressure" without naming who the competitor is and what they do better.
- NEVER list a strength that is actually aspirational (e.g., "innovative culture" for a company that has not built anything yet).
- NEVER list an opportunity that has no pathway to capture (e.g., "international expansion" for a pre-revenue startup with no localisation plan).
- NEVER write strategic_implications that are just restatements of the SWOT items. Implications must be NEW reasoning derived from combining multiple items.

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If PEST analysis and Five Forces data are both empty/missing, flag as FATAL: "Cannot synthesize SWOT without environment research from Section 3."
- If there is zero internal data (no capability_gaps, no org_structure), the Strengths and Weaknesses quadrants must be flagged as "speculative — based on opportunity description only, not validated internal data."

## Rules:
- Each SWOT quadrant must have at least 2 items
- Each item must include evidence that cites which prior section it came from
- strategic_implications must be at least 100 characters
- priority_strategic_issues must have at least 2 items, phrased as decisions
- Cross-reference: strengths should relate to capabilities, threats to five forces, etc.
- If Section 4 data is unavailable, derive weaknesses from opportunity gaps instead

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, strengths, weaknesses, opportunities, threats, strategic_implications, priority_strategic_issues, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
"""


class SWOTSynthesizerAgent(BaseChildAgent):
    SYSTEM_PROMPT = SYSTEM_PROMPT
    AGENT_NAME = "SWOTSynthesizer"
    AGENT_ROLE = (
        "SWOT Synthesizer — you combine PEST analysis, Five Forces, and org capabilities "
        "into a coherent SWOT matrix with strategic implications and priority issues"
    )
    SECTION_NUMBER = "5"
    MODEL_ENV = "CLAUDE_SONNET_MODEL"
    MODEL_DEFAULT = "claude-sonnet-4-20250514"
    INPUT_SCHEMA = SWOTSynthesizerInput
    OUTPUT_SCHEMA = SWOTSynthesizerOutput

    def _default_gap_key(self) -> str:
        return "pest_analysis"

    def _rag_enrich(self) -> str:
        return rag_enrich(
            "SWOT strengths weaknesses opportunities threats strategic implications",
            section="5",
        )

    def _extract_input(self, input_package: dict, task: dict) -> dict:
        return {
            "pest_analysis": input_package.get("pest_analysis", []),
            "five_forces": input_package.get("five_forces", []),
            "risks_opportunities": input_package.get("risks_opportunities", {}),
            "capability_gaps": input_package.get("capability_gaps", []),
            "org_structure": input_package.get("org_structure", ""),
            "opportunity_description": input_package.get("opportunity_description", ""),
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

    def _build_ie_input_data(self, input_package: dict) -> dict:
        return {
            "pest_analysis": input_package.get("pest_analysis", []),
            "five_forces": input_package.get("five_forces", []),
            "risks_opportunities": input_package.get("risks_opportunities", {}),
            "capability_gaps": input_package.get("capability_gaps", []),
            "org_structure": input_package.get("org_structure", ""),
            "opportunity_description": input_package.get("opportunity_description", ""),
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
            "pest_analysis": original_output.get("pest_analysis_input", []),
            "five_forces": original_output.get("five_forces_input", []),
            "risks_opportunities": original_output.get("risks_opportunities_input", {}),
            "capability_gaps": [],
            "org_structure": "",
            "opportunity_description": original_output.get("opportunity_description", ""),
            "revision_required": True,
            "revision_feedback": f"COUNCIL REVIEW FEEDBACK:\n{revision_instructions}\n\nSPECIFIC CRITIQUES:\n{critique_text}",
            "cross_section_context": content.get("cross_section_context", {}),
        }

        revised_content = {"task": {"input_package": input_package, "task_id": task_id}}
        await self.handle_request(task_id, session_id, pipeline_run_id, revised_content)

    async def _post_process(self, task_id: str, session_id: str, pipeline_run_id: str, validated_input: SWOTSynthesizerInput, result: dict):
        """Detect if SWOT threats are unaddressed by org capabilities; propose a fix to org designer."""
        threats = result.get("threats", [])
        high_threats = [t for t in threats if isinstance(t, dict) and t.get("impact") == "high"]
        capability_gaps = validated_input.capability_gaps or []
        gap_descriptions = [g.get("gap", "").lower() if isinstance(g, dict) else "" for g in capability_gaps]

        unaddressed = []
        for threat in high_threats:
            item = threat.get("item", "").lower()
            addressed = any(gap in item or item in gap for gap in gap_descriptions if gap)
            if not addressed:
                unaddressed.append(threat.get("item", ""))

        if len(unaddressed) >= 2 and self._bus is not None:
            msg = ACLMessage(
                sender="swot_synthesizer",
                receiver="mother_agent",
                performative="propose",
                content={
                    "target_agent": "organisation_designer",
                    "proposal": f"SWOT found {len(unaddressed)} high-severity threats with no matching "
                                f"capability in org structure: {', '.join(unaddressed[:3])}. "
                                f"Suggest adding capability_gaps or roles to address these.",
                    "field": "capability_gaps",
                    "evidence": {"unaddressed_threats": unaddressed[:3]},
                },
                task_id=task_id,
                session_id=session_id,
                pipeline_run_id=pipeline_run_id,
            )
            await self._bus.send(msg)
            logger.info("[SWOTSynthesizer] Proposed capability gap addition to org designer")

    async def _send_inform(self, task_id: str, session_id: str, pipeline_run_id: str, output: dict):
        """SWOT output goes to the Council Agent."""
        content = {"output": output, "section_number": self.SECTION_NUMBER, "agent_name": "swot_synthesizer"}
        if self._bus is not None:
            msg = ACLMessage(
                sender="swot_synthesizer",
                receiver="council_agent",
                performative="inform",
                content=content,
                task_id=task_id,
                session_id=session_id,
                pipeline_run_id=pipeline_run_id,
            )
            await self._bus.send(msg)
            return
        logger.error("[SWOTSynthesizer] No message bus — cannot send inform")

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

    def _build_prompt(self, validated_input: SWOTSynthesizerInput) -> str:
        return f"""Synthesize a SWOT matrix from these inputs.

PEST ANALYSIS: {json.dumps(validated_input.pest_analysis, indent=2)}
FIVE FORCES: {json.dumps(validated_input.five_forces, indent=2)}
RISKS & OPPORTUNITIES: {json.dumps(validated_input.risks_opportunities, indent=2)}
CAPABILITY GAPS: {json.dumps(validated_input.capability_gaps, indent=2)}
ORG STRUCTURE: {validated_input.org_structure}
OPPORTUNITY: {validated_input.opportunity_description}

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

    def _parse_llm_response(self, raw: str, validated_input: SWOTSynthesizerInput) -> dict:
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(validated_input),
            agent_name="SWOTSynthesizer",
        )
        if result is not None:
            return result

        logger.warning("[SWOTSynthesizer] Both parse attempts failed, using fallback")
        return self._fallback_defaults(validated_input)

    def _fallback_defaults(self, validated_input: SWOTSynthesizerInput) -> dict:
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
