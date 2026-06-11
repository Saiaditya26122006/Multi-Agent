import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging

from agents.phase2.base_child_agent import BaseChildAgent, run_child_agent
from schemas.inputs.rd_technology import RDTechnologyInput
from schemas.outputs.rd_technology import RDTechnologyOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the R&D & Technology agent in a multi-agent business plan system.
Your role: assess technology readiness, design development roadmap, evaluate IP defensibility, and identify technical risks for technology-driven or patent-based businesses.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. TECHNOLOGY READINESS LEVEL (TRL) — Use NASA/DOD standard
   - **TRL 1-3**: Basic research, proof of concept → HIGH RISK, 3-5 years to market
   - **TRL 4-6**: Prototype, pilot testing → MEDIUM RISK, 1-3 years to market
   - **TRL 7-9**: Product ready, deployed → LOW RISK, <1 year to market
   - If technology is TRL 1-3 and business plan assumes revenue in Year 1, flag as FATAL contradiction
   - State TRL level explicitly in rd_plan with justification

2. IP STRENGTH & DEFENSIBILITY (Patent ≠ automatic moat)
   - **Patent filed + granted**: Strong defensibility IF claims are broad and enforceable
   - **Patent filed, pending**: Medium defensibility — claims may narrow during examination
   - **Provisional patent**: Weak — gives 12 months to file full patent, no protection yet
   - **Trade secret**: Medium IF secret is non-obvious and hard to reverse-engineer
   - **No formal IP**: Weak — first-mover advantage only, easily copied
   - **Freedom to operate**: Are you infringing on existing patents? If uncertain, flag as risk
   - NEVER write "strong IP position" without stating patent number or specific claims

3. DEVELOPMENT COST REALISM (R&D is expensive)
   - **Hardware/Biotech**: $2M-$10M+ to first commercial product (prototyping, testing, regulatory)
   - **Deep Tech (AI/ML models)**: $500K-$3M (data, compute, talent, iteration)
   - **Software innovation**: $200K-$1M (engineering time, infrastructure)
   - R&D burn rate must match Financial model (Section 12) — if Financial shows $100K/mo burn but R&D needs $500K/mo, flag inconsistency
   - Cost estimate should include: personnel (engineers, researchers), equipment, materials, testing, regulatory approval (if applicable)

4. TIMELINE TO MARKET (Most teams underestimate by 2-3x)
   - **Regulatory approval** (FDA, CE mark, etc.): Add 12-24 months
   - **Manufacturing scale-up** (hardware): Add 6-12 months
   - **Clinical trials** (medtech/biotech): Add 24-36 months
   - **AI model training + deployment**: Add 6-12 months for production-grade
   - If technology_description says "nearly ready" but is TRL 4-5, reality is 12-18 months minimum

5. TECHNICAL RISK IDENTIFICATION (What could go wrong?)
   - **Specific failure modes**: "Model accuracy <85% on real-world data" NOT "technology may not work"
   - **Dependency risks**: "Relies on third-party API with no SLA" NOT "partnerships may fail"
   - **Scalability risks**: "Database latency >500ms at 10K concurrent users" NOT "may not scale"
   - **Regulatory risks**: "FDA 510(k) approval may take 18 months, blocking revenue" NOT "regulatory uncertainty"
   - NEVER write generic risks — be specific about what breaks and why

## ANTI-PATTERNS — If you catch yourself writing any of these, you do not have enough information:
- NEVER write "cutting-edge technology" without naming the specific innovation
- NEVER write "patent-pending" without stating what the patent covers
- NEVER write "12 months to market" for TRL 3 technology — that is not realistic
- NEVER write "strong IP portfolio" for a single provisional patent
- NEVER write "scalable architecture" without specifying what scale (10K users? 1M? 100M?)
- NEVER write "proven technology" for TRL 4-5 — proven means TRL 8-9

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If technology_description is empty AND ip_status is empty, escalate with trigger "unclear_input" and gap_key "technology_description".
- If business is not technology-driven (e.g., pure services, distribution play), flag: "Section 6 not applicable — no core technology or IP."

## Rules:
- rd_plan must include: development_stages, timeline_to_market (months), cost_estimate (range), trl_level (1-9)
- ip_analysis must include: patent_status, defensibility_score ("strong"/"medium"/"weak"), freedom_to_operate, competitive_ip_landscape
- technology_risk must be at least 50 characters — specific failure modes, not generic
- development_milestones must have at least 1 entry with stage_name, duration_months, cost_estimate, success_criteria
- If patent details are unavailable, label ip_analysis defensibility as "unknown — requires patent attorney review"

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, rd_plan, ip_analysis, technology_risk, development_milestones, technical_dependencies, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
"""


class RDTechnologyAgent(BaseChildAgent):

    SYSTEM_PROMPT = SYSTEM_PROMPT
    AGENT_NAME = "RDTechnology"
    AGENT_ROLE = (
        "R&D & Technology — you assess technology readiness, design development roadmap, "
        "evaluate IP defensibility, and identify technical risks"
    )
    SECTION_NUMBER = "6"
    MODEL_ENV = "CLAUDE_HAIKU_MODEL"
    MODEL_DEFAULT = "claude-haiku-4-5-20251001"
    INPUT_SCHEMA = RDTechnologyInput
    OUTPUT_SCHEMA = RDTechnologyOutput

    def _default_gap_key(self) -> str:
        return "technology_description"

    def _extract_input(self, input_package: dict, task: dict) -> dict:
        return {
            "opportunity_description": input_package.get("opportunity_description", ""),
            "competitive_strategy": input_package.get("competitive_strategy", ""),
            "technology_description": input_package.get("technology_description", ""),
            "ip_status": input_package.get("ip_status", ""),
            "patent_details": input_package.get("patent_details", ""),
            "technical_milestones": input_package.get("technical_milestones", ""),
            "industry_sector": input_package.get("industry_sector", ""),
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

    def _build_ie_input_data(self, input_package: dict) -> dict:
        return {
            "opportunity_description": input_package.get("opportunity_description", ""),
            "competitive_strategy": input_package.get("competitive_strategy", ""),
            "technology_description": input_package.get("technology_description", ""),
            "ip_status": input_package.get("ip_status", ""),
            "patent_details": input_package.get("patent_details", ""),
            "technical_milestones": input_package.get("technical_milestones", ""),
            "industry_sector": input_package.get("industry_sector", ""),
            "acceptance_criteria": input_package.get("acceptance_criteria", ""),
        }

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON with these exact keys:
- section_number: "6"
- rd_plan: dict with "development_stages" (list), "timeline_to_market" (months), "cost_estimate" (range), "trl_level" (1-9 with justification)
- ip_analysis: dict with "patent_status", "defensibility_score" ("strong"/"medium"/"weak"), "freedom_to_operate", "competitive_ip_landscape"
- technology_risk: str (min 50 chars — specific failure modes)
- development_milestones: list of {"stage_name": str, "description": str (min 30 chars), "duration_months": int (1-60), "cost_estimate": str, "success_criteria": str} (min 1 item)
- technical_dependencies: [str] (external dependencies)
- assumptions_used: list of {"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0"""

    def _build_prompt(self, inp: RDTechnologyInput) -> str:
        return f"""Assess the technology readiness and development plan for this business.

OPPORTUNITY: {inp.opportunity_description}
COMPETITIVE STRATEGY: {inp.competitive_strategy}

TECHNOLOGY DESCRIPTION: {inp.technology_description or "Not provided"}
IP STATUS: {inp.ip_status or "Not provided"}
PATENT DETAILS: {inp.patent_details or "Not provided"}
TECHNICAL MILESTONES: {inp.technical_milestones or "Not provided"}

INDUSTRY SECTOR: {inp.industry_sector or "Not provided"}

Return ONLY valid JSON with these exact keys:
- section_number: "6"
- rd_plan: dict with "development_stages", "timeline_to_market", "cost_estimate", "trl_level"
- ip_analysis: dict with "patent_status", "defensibility_score", "freedom_to_operate", "competitive_ip_landscape"
- technology_risk: str (min 50 chars)
- development_milestones: list of {{"stage_name": str, "description": str (min 30 chars), "duration_months": int, "cost_estimate": str, "success_criteria": str}}
- technical_dependencies: [str]
- assumptions_used: list of {{"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0
"""

    def _fallback_defaults(self, inp: RDTechnologyInput) -> dict:
        return {
            "section_number": "6",
            "rd_plan": {
                "development_stages": [
                    "Stage 1: Prototype development and validation",
                    "Stage 2: Pilot testing with initial customers",
                    "Stage 3: Production-ready system and scaling",
                ],
                "timeline_to_market": "12-18 months",
                "cost_estimate": "$300K-$800K (depends on technical complexity)",
                "trl_level": "TRL 5-6 (prototype stage) — requires validation before commercial deployment",
            },
            "ip_analysis": {
                "patent_status": "Unknown — requires CEO input on patent filings",
                "defensibility_score": "medium",
                "freedom_to_operate": "Unknown — requires patent attorney review of competitive landscape",
                "competitive_ip_landscape": "Competitive analysis incomplete — need to assess existing patents in this domain",
            },
            "technology_risk": (
                "Technical validation risk: unproven at scale with real-world data. "
                "Integration risk: dependencies on third-party APIs or infrastructure. "
                "Timeline risk: development typically takes 1.5-2x initial estimates."
            ),
            "development_milestones": [
                {
                    "stage_name": "Prototype Development",
                    "description": "Build initial working prototype demonstrating core technical capability",
                    "duration_months": 6,
                    "cost_estimate": "$150K-$300K",
                    "success_criteria": "Core functionality working in controlled environment",
                },
                {
                    "stage_name": "Pilot Testing",
                    "description": "Deploy prototype with 3-5 pilot customers, collect feedback, iterate",
                    "duration_months": 6,
                    "cost_estimate": "$100K-$200K",
                    "success_criteria": "80%+ customer satisfaction, identified production requirements",
                },
            ],
            "technical_dependencies": [
                "Third-party APIs or infrastructure (cloud, data providers)",
                "Regulatory approval if applicable",
                "Access to domain-specific data or expertise",
            ],
            "assumptions_used": [{
                "statement": "LLM output was unparseable — defaults used",
                "confidence": "low",
                "source": "assumed",
                "source_detail": None
            }],
            "uncertainties": [
                "LLM response could not be parsed — full analysis not completed",
                "Technology readiness level unknown without detailed technical review",
                "IP defensibility unknown without patent details",
            ],
            "confidence_score": "low",
            "input_tokens": 0,
            "output_tokens": 0,
        }


async def main():
    await run_child_agent(RDTechnologyAgent, "RD_TECHNOLOGY_JID", "RD_TECHNOLOGY_PASSWORD")


if __name__ == "__main__":
    asyncio.run(main())
