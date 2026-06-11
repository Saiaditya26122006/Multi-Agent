import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging

from agents.phase2.base_child_agent import BaseChildAgent, run_child_agent
from schemas.inputs.entrepreneur_team import EntrepreneurTeamInput
from schemas.outputs.entrepreneur_team import EntrepreneurTeamOutput

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Entrepreneur & Development Team agent in a multi-agent business plan system.
Your role: assess the founding team's credibility, identify strengths and gaps, and determine whether this team can execute on the opportunity.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. FOUNDER-MARKET FIT (Does the founder's background match the problem?)
   - What specific experience does the founder have that makes them uniquely qualified for THIS problem domain?
   - If the founder is pivoting from a different industry, what transferable skills justify the pivot?
   - Example: "SaaS founder building SaaS" = high fit. "Doctor building fintech" = requires specific explanation of why medical background helps.
   - NEVER write "relevant experience" without naming what experience and why it's relevant.

2. COMPLEMENTARY SKILLS (Does the team cover product, sales, ops?)
   - Assess coverage across: product/engineering, GTM/sales, operations/finance, domain expertise.
   - A founding team of 3 engineers building B2B software is missing GTM — this is a gap.
   - A solo founder in a complex market (e.g., regulated healthcare) is a structural risk.
   - Rate team completeness: "complete" (all bases covered), "adequate" (1-2 minor gaps), "incomplete" (major capability missing).

3. EXECUTION TRACK RECORD (Have they launched anything before?)
   - First-time founder with no prior launches = higher risk, not disqualifying.
   - Prior exits or successful launches = credibility boost.
   - Prior failures that taught relevant lessons = neutral to positive (depends on what they learned).
   - If no track record exists, state this plainly — do not fabricate.

4. GAP ANALYSIS (What roles are missing and how critical are they?)
   - Identify gaps in: technical leadership, sales/BD, marketing, ops, finance, domain expertise.
   - Label each gap as: "critical" (must hire pre-launch), "important" (must hire within 6 months), "nice-to-have" (can defer).
   - team_gaps must be actionable — "needs sales leader with enterprise SaaS experience" is good, "needs more people" is not.

5. RED FLAGS (Structural team risks)
   - Solo founder in complex/regulated market → high execution risk
   - All-technical team with zero GTM experience → distribution risk
   - Remote team across 5+ time zones with no prior collaboration → coordination risk
   - Founder with no equity stake → misaligned incentives
   - Co-founder equity split disputes unresolved → ticking time bomb
   - NEVER ignore red flags. If they exist, state them in execution_risks.

## ANTI-PATTERNS — If you catch yourself writing any of these, you do not have enough information:
- NEVER write "experienced founder" without naming the specific experience.
- NEVER write "strong technical background" without naming technologies/domains.
- NEVER write "well-rounded team" without naming which roles are covered.
- NEVER write "passionate about the problem" — passion is not a credential.
- NEVER write "complementary skill sets" without listing what those skills are.

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If founder_profile is entirely empty AND team_composition is entirely empty, escalate with trigger "unclear_input" and gap_key "founder_profile".
- If the founder profile exists but contains <10 characters of substance, escalate.

## Rules:
- founder_profiles must have at least 1 entry with background (min 20 chars) and relevant_experience (min 30 chars)
- team_strengths must have at least 2 entries that are specific capabilities, not generic ("technical expertise" is too vague)
- team_gaps must have at least 1 entry that is actionable (feeds Section 11 HR plan)
- team_credibility_assessment must be at least 100 characters — synthesize the overall picture
- If information is missing, state it as an uncertainty rather than fabricating
- credibility_score per founder: "high" = proven track record in this domain, "medium" = relevant but unproven, "low" = weak fit

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, founder_profiles, team_strengths, team_gaps, team_credibility_assessment, execution_risks, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
"""


class EntrepreneurTeamAgent(BaseChildAgent):

    SYSTEM_PROMPT = SYSTEM_PROMPT
    AGENT_NAME = "EntrepreneurTeam"
    AGENT_ROLE = (
        "Entrepreneur & Development Team — you assess the founding team's credibility, "
        "identify strengths and gaps, and determine execution capability"
    )
    SECTION_NUMBER = "2"
    MODEL_ENV = "CLAUDE_HAIKU_MODEL"
    MODEL_DEFAULT = "claude-haiku-4-5-20251001"
    INPUT_SCHEMA = EntrepreneurTeamInput
    OUTPUT_SCHEMA = EntrepreneurTeamOutput

    def _default_gap_key(self) -> str:
        return "founder_profile"

    def _extract_input(self, input_package: dict, task: dict) -> dict:
        founder_profile = input_package.get("founder_profile", "")
        team_composition = input_package.get("team_composition", {})
        founder_background = input_package.get("founder_background", "")
        relevant_experience = input_package.get("relevant_experience", "")

        # If founder_profile is missing but other fields exist, synthesize
        if not founder_profile and (founder_background or relevant_experience):
            founder_profile = f"Background: {founder_background}. Experience: {relevant_experience}"

        return {
            "opportunity_description": input_package.get("opportunity_description", ""),
            "competitive_strategy": input_package.get("competitive_strategy", ""),
            "icp_hypothesis": input_package.get("icp_hypothesis", {}),
            "founder_profile": founder_profile,
            "team_composition": team_composition,
            "founder_background": founder_background,
            "relevant_experience": relevant_experience,
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

    def _build_ie_input_data(self, input_package: dict) -> dict:
        return {
            "opportunity_description": input_package.get("opportunity_description", ""),
            "competitive_strategy": input_package.get("competitive_strategy", ""),
            "icp_hypothesis": input_package.get("icp_hypothesis", {}),
            "founder_profile": input_package.get("founder_profile", ""),
            "team_composition": input_package.get("team_composition", {}),
            "founder_background": input_package.get("founder_background", ""),
            "relevant_experience": input_package.get("relevant_experience", ""),
            "acceptance_criteria": input_package.get("acceptance_criteria", ""),
        }

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON with these exact keys:
- section_number: "2"
- founder_profiles: list of {"name": str, "role": str, "background": str (min 20 chars), "relevant_experience": str (min 30 chars), "credibility_score": "high"|"medium"|"low", "founder_market_fit": str (min 30 chars)}
- team_strengths: [str] (min 2 items — specific capabilities like "5 years enterprise SaaS sales", not generic)
- team_gaps: [str] (min 1 item — actionable gaps like "needs VP Sales with B2B SaaS experience")
- team_credibility_assessment: str (min 100 chars — overall assessment of team fit)
- execution_risks: [str] (team-related risks: solo founder, no GTM, etc.)
- assumptions_used: list of {"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0"""

    def _build_prompt(self, inp: EntrepreneurTeamInput) -> str:
        return f"""Assess this founding team for the given business opportunity.

OPPORTUNITY: {inp.opportunity_description}
COMPETITIVE STRATEGY: {inp.competitive_strategy}
ICP: {json.dumps(inp.icp_hypothesis, indent=2)}

FOUNDER PROFILE: {inp.founder_profile or "Not provided"}
TEAM COMPOSITION: {json.dumps(inp.team_composition, indent=2) if inp.team_composition else "Not provided"}

Return ONLY valid JSON with these exact keys:
- section_number: "2"
- founder_profiles: list of {{"name": str, "role": str, "background": str (min 20 chars), "relevant_experience": str (min 30 chars), "credibility_score": "high"|"medium"|"low", "founder_market_fit": str (min 30 chars)}}
- team_strengths: [str] (min 2 items, specific capabilities)
- team_gaps: [str] (min 1 item, actionable gaps)
- team_credibility_assessment: str (min 100 chars)
- execution_risks: [str]
- assumptions_used: list of {{"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0
"""

    def _fallback_defaults(self, inp: EntrepreneurTeamInput) -> dict:
        return {
            "section_number": "2",
            "founder_profiles": [{
                "name": "Founder",
                "role": "CEO / Founder",
                "background": "Background not provided — assessment pending founder input",
                "relevant_experience": "Experience details not provided — requires clarification from founder",
                "credibility_score": "low",
                "founder_market_fit": "Cannot assess fit without founder background information",
            }],
            "team_strengths": [
                "Opportunity identified and validated through Phase 1",
                "Commitment to building this business",
            ],
            "team_gaps": [
                "Founder background and relevant experience not provided",
                "Team composition unknown — cannot assess coverage of key functions",
            ],
            "team_credibility_assessment": (
                "Team credibility cannot be assessed without founder profile and team composition data. "
                "This section requires input from the founder about their background, relevant experience, "
                "and current team members. Recommend escalating to collect this information before proceeding."
            ),
            "execution_risks": [
                "Team assessment incomplete — founder profile missing",
                "Unable to identify execution risks without team information",
            ],
            "assumptions_used": [{
                "statement": "LLM output was unparseable — defaults used",
                "confidence": "low",
                "source": "assumed",
                "source_detail": None
            }],
            "uncertainties": [
                "LLM response could not be parsed — full analysis not completed",
                "Founder background unknown",
                "Team composition unknown",
            ],
            "confidence_score": "low",
            "input_tokens": 0,
            "output_tokens": 0,
        }


async def main():
    await run_child_agent(EntrepreneurTeamAgent, "ENTREPRENEUR_TEAM_JID", "ENTREPRENEUR_TEAM_PASSWORD")


if __name__ == "__main__":
    asyncio.run(main())
