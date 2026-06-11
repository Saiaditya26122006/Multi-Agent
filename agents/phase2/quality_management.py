import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging

from agents.phase2.base_child_agent import BaseChildAgent, run_child_agent
from schemas.inputs.quality_management import QualityManagementInput
from schemas.outputs.quality_management import QualityManagementOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Quality Management agent in a multi-agent business plan system.
Your role: design quality assurance approach, define quality procedures, and establish quality metrics for service businesses where delivery consistency is a key differentiator.

## REASONING FRAMEWORK:

1. QUALITY AS DIFFERENTIATOR (Not just "we care about quality")
   - What specifically breaks if quality is inconsistent? (customer churn, reputation damage, compliance risk)
   - How is quality measured? (SLAs, customer satisfaction scores, error rates, turnaround time)
   - Who enforces quality? (QA team, automated checks, peer review, audits)

2. QUALITY PROCEDURES (Must be specific and auditable)
   - Good: "Every deliverable reviewed by senior consultant before client delivery, using 15-point checklist"
   - Bad: "Maintain high quality standards through rigorous review"
   - Procedures must answer: WHO checks, WHEN, HOW, and what happens if quality fails

3. QUALITY METRICS (Observable and actionable)
   - SLAs: "99.5% uptime" or "24-hour response time"
   - Customer satisfaction: "NPS >40" or "CSAT >4.5/5"
   - Error rates: "<2% defect rate" or "zero critical bugs in production"
   - If quality cannot be measured, it cannot be managed

## ANTI-PATTERNS:
- NEVER write "commitment to excellence" — state specific procedures
- NEVER write "quality first" — state how quality is enforced
- NEVER write "customer satisfaction" without stating how it's measured

## RULES:
- quality_policy must be at least 100 characters
- quality_procedures must have at least 2 specific, auditable procedures
- quality_metrics should include at least 1 measurable KPI

You must respond with ONLY valid JSON: section_number, quality_policy, quality_procedures, quality_metrics, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
"""


class QualityManagementAgent(BaseChildAgent):

    SYSTEM_PROMPT = SYSTEM_PROMPT
    AGENT_NAME = "QualityManagement"
    AGENT_ROLE = "Quality Management — quality assurance approach, procedures, metrics"
    SECTION_NUMBER = "9"
    MODEL_ENV = "CLAUDE_HAIKU_MODEL"
    MODEL_DEFAULT = "claude-haiku-4-5-20251001"
    INPUT_SCHEMA = QualityManagementInput
    OUTPUT_SCHEMA = QualityManagementOutput

    def _default_gap_key(self) -> str:
        return "service_description"

    def _extract_input(self, input_package: dict, task: dict) -> dict:
        return {
            "opportunity_description": input_package.get("opportunity_description", ""),
            "service_description": input_package.get("service_description", ""),
            "target_market_analysis": input_package.get("target_market_analysis", {}),
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

    def _build_ie_input_data(self, input_package: dict) -> dict:
        return self._extract_input(input_package, {})

    def _build_schema_prompt(self) -> str:
        return """Return JSON: section_number, quality_policy (min 100 chars), quality_procedures (min 2 items), quality_metrics, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens"""

    def _build_prompt(self, inp: QualityManagementInput) -> str:
        return f"""Design quality management approach.

OPPORTUNITY: {inp.opportunity_description}
SERVICE: {inp.service_description or "Not provided"}

Return JSON with: section_number, quality_policy, quality_procedures, quality_metrics, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens
"""

    def _fallback_defaults(self, inp: QualityManagementInput) -> dict:
        return {
            "section_number": "9",
            "quality_policy": "Quality assurance through systematic review processes, customer feedback loops, and continuous improvement. Every deliverable meets defined standards before release.",
            "quality_procedures": [
                "Peer review of all deliverables before customer delivery",
                "Monthly customer satisfaction surveys with NPS tracking",
            ],
            "quality_metrics": [{"metric": "Customer satisfaction", "target": "CSAT >4.5/5"}],
            "assumptions_used": [{"statement": "Fallback", "confidence": "low", "source": "assumed", "source_detail": None}],
            "uncertainties": ["Quality procedures incomplete"],
            "confidence_score": "low",
            "input_tokens": 0,
            "output_tokens": 0,
        }


async def main():
    await run_child_agent(QualityManagementAgent, "QUALITY_MANAGEMENT_JID", "QUALITY_MANAGEMENT_PASSWORD")


if __name__ == "__main__":
    asyncio.run(main())
