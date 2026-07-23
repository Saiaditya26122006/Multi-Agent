"""Generic Analyst agent — writes any custom section Alex adds (Build v2 Phase 4).

The built-in specialists are topic-locked (one class per business-plan chapter
with a strict schema). This one is topic-agnostic: it receives the section's
title at runtime and produces a grounded, Council-checkable analysis for it, so
Alex can commission a specialist for a topic that isn't in the fixed 15.

It inherits the same data-gathering tools (RAG, web research, data requests) and
the same grounding + quality gate as every other agent — nothing special wired.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import logging

from agents.phase2.base_child_agent import BaseChildAgent, run_child_agent
from schemas.inputs.generic_analyst import GenericAnalystInput
from schemas.outputs.generic_analyst import GenericAnalystOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a rigorous business-plan analyst in a multi-agent system.
You are given a SECTION TITLE (the topic) and the business idea, and you write
that one section of the plan.

## RULES
- Ground every factual claim in the idea and the provided CEO data — never invent
  numbers, market sizes, or competitor facts. If you must assume, say so and list
  it under assumptions_used.
- narrative must be specific to THIS section's topic and at least a few substantial
  paragraphs. No generic filler ("leverage synergies", "world-class"), no restating
  the topic back.
- key_points: the 3-7 concrete takeaways a CEO would act on.
- uncertainties: what you could not verify and what data would resolve it.
- confidence_score: "low" | "medium" | "high" — be honest; low if the idea data is thin.

You must respond with ONLY valid JSON containing: section_number, title, narrative,
key_points, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
"""


class GenericAnalystAgent(BaseChildAgent):

    SYSTEM_PROMPT = SYSTEM_PROMPT
    AGENT_NAME = "Generic Analyst"
    AGENT_ROLE = "Custom-section analyst — writes any business-plan section on demand"
    SECTION_NUMBER = "custom"
    MODEL_ENV = "CLAUDE_HAIKU_MODEL"
    MODEL_DEFAULT = "claude-haiku-4-5-20251001"
    INPUT_SCHEMA = GenericAnalystInput
    OUTPUT_SCHEMA = GenericAnalystOutput

    def _default_gap_key(self) -> str:
        return "context"

    def _extract_input(self, input_package: dict, task: dict) -> dict:
        return {
            "section_title": input_package.get("section_title", "Custom section"),
            "idea": input_package.get("idea", ""),
            "context": input_package.get("context", "")
            or _summarize_prior(input_package.get("cross_section_context", {})),
            "focus": input_package.get("focus_directive", "")
            or input_package.get("focus", ""),
        }

    def _build_ie_input_data(self, input_package: dict) -> dict:
        return self._extract_input(input_package, {})

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON:
- section_number: "custom"
- title: str (the section title)
- narrative: str (min 100 chars — the analysis, specific to the topic)
- key_points: [str]  (3-7 concrete takeaways)
- assumptions_used: [str]
- uncertainties: [str]
- confidence_score: "low" | "medium" | "high"
- input_tokens, output_tokens"""

    def _build_prompt(self, inp: GenericAnalystInput) -> str:
        focus = f"\nFOCUS (Alex asked you to emphasise): {inp.focus}" if inp.focus else ""
        return f"""Write the business-plan section titled: {inp.section_title}

BUSINESS IDEA: {inp.idea or "(not specified)"}
CONTEXT FROM PRIOR SECTIONS: {inp.context or "(none yet)"}{focus}

Return JSON with: section_number, title, narrative, key_points, assumptions_used,
uncertainties, confidence_score, input_tokens, output_tokens
"""

    def _fallback_defaults(self, inp: GenericAnalystInput) -> dict:
        title = getattr(inp, "section_title", "Custom section")
        return {
            "section_number": "custom",
            "title": title,
            "narrative": (
                f"This section ('{title}') could not be completed with confidence "
                "because the current idea data is too thin to support specific, "
                "evidence-backed claims. Provide the missing details via Feed and "
                "re-run so this can be written with real grounding rather than a "
                "placeholder."
            ),
            "key_points": ["Insufficient data — awaiting Alex's input via Feed"],
            "assumptions_used": ["Fallback used — no reliable data available"],
            "uncertainties": [f"All specifics of '{title}' remain unverified"],
            "confidence_score": "low",
            "input_tokens": 0,
            "output_tokens": 0,
        }


def _summarize_prior(cross_context: dict) -> str:
    """Compress prior section outputs into a short context string."""
    if not isinstance(cross_context, dict) or not cross_context:
        return ""
    keys = [str(k) for k in list(cross_context.keys())[:8]]
    return "Prior sections drafted: " + ", ".join(keys)


async def main():
    await run_child_agent(GenericAnalystAgent, "GENERIC_ANALYST_JID", "GENERIC_ANALYST_PASSWORD")


if __name__ == "__main__":
    asyncio.run(main())
