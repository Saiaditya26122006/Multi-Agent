"""Environment Research Agent — PEST analysis, Porter's Five Forces, market sizing."""

import json
import logging
import os
from datetime import datetime

from agents.phase2.base_child_agent import BaseChildAgent
from agents.phase2.llm_utils import parse_json_with_retry
from schemas.inputs.environment_research import EnvironmentResearchInput
from schemas.outputs.environment_research import EnvironmentResearchOutput
from services.search_service import search_for_section

logger = logging.getLogger(__name__)

SEARCH_QUERIES = {
    "3": [
        "EU AI Act academic research software compliance 2025",
        "GDPR SaaS academic procurement requirements Europe",
        "European academic publishing market regulation 2025"
    ]
}


def _get_live_market_data(section_number: str) -> str:
    """Run section-specific queries and format results for prompt injection."""
    queries = SEARCH_QUERIES.get(section_number, [])
    if not queries:
        return ""

    all_results = []
    for query in queries:
        results = search_for_section(section_number, query)
        all_results.extend(results)

    if not all_results:
        return "No live market data retrieved for this section."

    lines = [f"Retrieved {datetime.utcnow().strftime('%Y-%m-%d')}:"]
    for i, r in enumerate(all_results[:8], 1):
        lines.append(
            f"[{i}] {r['title']} — {r['snippet'][:200]} "
            f"(Source: {r['url']}, Freshness: {r['freshness']})"
        )
    return "\n".join(lines)

SYSTEM_PROMPT = """You are the Environment Research agent in a multi-agent business plan system.
Your role: conduct rigorous external environment analysis — PEST, Porter's Five Forces, market sizing,
and regulatory landscape — with specific evidence for every claim.

## REASONING FRAMEWORK — Apply each lens with evidence requirements:

1. PEST ANALYSIS (Each factor needs specifics, not labels)
   - Political: Name the specific regulation, policy, or government body. Cite the jurisdiction. If you write "favourable regulatory environment" without naming the regulation, you have failed.
   - Economic: Cite specific economic indicators (interest rates, GDP growth, sector spending). "Growing economy" is not analysis.
   - Social: Name the demographic shift, consumer behaviour change, or cultural trend. Quantify it if possible (e.g., "65% of millennials prefer X" is better than "younger consumers want X").
   - Technological: Name the specific technology, adoption curve stage, and who is deploying it. "AI is transforming industries" is useless — name which AI capability and which industry process.

2. FIVE FORCES (Each force needs a verdict AND the mechanism)
   - Threat of new entrants: What are the specific barriers? Capital requirements in dollars? Regulatory licences needed? Time to build?
   - Supplier power: Who are the actual suppliers? How many alternatives exist? What is the switching cost?
   - Buyer power: How concentrated are buyers? What percentage of revenue comes from top 10 customers? Can they backward-integrate?
   - Substitutes: Name the substitute. How close is it in functionality? What is the price difference?
   - Rivalry: How many direct competitors? What is their combined market share? Is the market growing fast enough to absorb new entrants?
   - NEVER rate a force as "medium" without explaining what specifically makes it medium rather than high or low.

3. MARKET SIZING (Bottom-up required)
   - Start from the specific customer segment, not the total addressable market.
   - Formula: (Number of potential customers in segment) x (realistic conversion rate) x (annual spend per customer).
   - If you cannot estimate any of these three numbers, say so — do not cite a "market size" from a generic report without connecting it to this specific business.

4. REGULATORY SCAN
   - Name specific regulatory bodies that govern this space.
   - Identify compliance requirements that affect go-to-market timeline or cost.
   - If the business operates across jurisdictions, flag which regulations differ.

5. EVIDENCE STANDARD
   - Every factor must have an "impact" (positive/negative/neutral) AND a reasoning chain: "Because [cause], this leads to [effect], which means [implication for this business]."
   - If you cannot complete that chain, the factor is speculative and must be labelled relevance: "low".

## ANTI-PATTERNS — If you catch yourself writing any of these, you do not have enough information:
- NEVER write "the market is growing rapidly" without a growth rate or named source.
- NEVER write "increasing demand for [product category]" without specifying who is demanding it and evidence of the increase.
- NEVER write "technology adoption is accelerating" without naming the technology and its current penetration rate.
- NEVER write "regulatory environment is supportive" without naming the specific regulation or policy.
- NEVER rate all five forces as "medium" — that is a cop-out. At least one force should be notably high or low for any real market.

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If market_scope is empty/vague AND business_type gives no clue about the operating environment, flag as FATAL: "Cannot conduct environment research without knowing the market or geography."
- If every PEST factor and every Five Force would require pure fabrication (no data points available), flag as FATAL.

## Rules:
- PEST must cover all 4 categories with at least one factor each
- Five Forces must cover all 5 forces with specific evidence, not just high/medium/low labels
- Market context must be at least 100 characters of substantive analysis
- Every assumption must be labelled with confidence and source
- If data is unavailable, state what is unknown rather than fabricating
- Focus on factors relevant to the specific market_scope and business_type provided

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, pest_analysis, five_forces, risks_opportunities, market_context, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
"""


class EnvironmentResearchAgent(BaseChildAgent):
    SYSTEM_PROMPT = SYSTEM_PROMPT
    AGENT_NAME = "EnvironmentResearch"
    AGENT_ROLE = "Environment Research — you conduct PEST analysis, Porter's Five Forces, and identify external risks and opportunities for the business"
    SECTION_NUMBER = "3"
    MODEL_ENV = "CLAUDE_HAIKU_MODEL"
    MODEL_DEFAULT = "claude-haiku-4-5-20251001"
    INPUT_SCHEMA = EnvironmentResearchInput
    OUTPUT_SCHEMA = EnvironmentResearchOutput

    def _default_gap_key(self) -> str:
        return "market_scope"

    def _extract_input(self, input_package: dict, task: dict) -> dict:
        return {
            "market_scope": input_package.get("market_scope", ""),
            "business_type": input_package.get("business_type", ""),
            "icp_hypothesis": input_package.get("icp_hypothesis", {}),
            "acceptance_criteria": task.get("acceptance_criteria", ""),
        }

    def _build_ie_input_data(self, input_package: dict) -> dict:
        return {
            "market_scope": input_package.get("market_scope", ""),
            "business_type": input_package.get("business_type", ""),
            "icp_hypothesis": input_package.get("icp_hypothesis", {}),
            "acceptance_criteria": input_package.get("acceptance_criteria", ""),
            "live_market_data": _get_live_market_data("3"),
        }

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON with these exact keys:
- section_number: "3"
- pest_analysis: list of {"category": "political"|"economic"|"social"|"technological", "factor": str, "impact": "positive"|"negative"|"neutral", "relevance": "high"|"medium"|"low"} (min 4 items, one per category)
- five_forces: list of {"force": str, "assessment": str, "strength": "high"|"medium"|"low"} (min 5 items)
- risks_opportunities: {"risks": [str], "opportunities": [str]}
- market_context: str (min 100 chars, synthesized external environment summary)
- assumptions_used: list of {"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0"""

    def _build_prompt(self, validated_input) -> str:
        return f"""Conduct environment research for this business.

MARKET SCOPE: {validated_input.market_scope}
BUSINESS TYPE: {validated_input.business_type}
ICP HYPOTHESIS: {json.dumps(validated_input.icp_hypothesis, indent=2)}

Return ONLY valid JSON with these exact keys:
- section_number: "3"
- pest_analysis: list of {{"category": "political"|"economic"|"social"|"technological", "factor": str, "impact": "positive"|"negative"|"neutral", "relevance": "high"|"medium"|"low"}} (min 4 items)
- five_forces: list of {{"force": str, "assessment": str, "strength": "high"|"medium"|"low"}} (min 5 items)
- risks_opportunities: {{"risks": [str], "opportunities": [str]}}
- market_context: str (min 100 chars, overall external environment summary)
- assumptions_used: list of {{"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0
"""

    def _parse_llm_response(self, raw: str, validated_input) -> dict:
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(validated_input),
            agent_name="EnvironmentResearch",
        )
        if result is not None:
            return result

        logger.warning("[EnvironmentResearch] Both parse attempts failed, using fallback")
        return self._fallback_defaults(validated_input)

    def _fallback_defaults(self, validated_input) -> dict:
        return {
            "section_number": "3",
            "pest_analysis": [
                {"category": "political", "factor": "Regulatory environment uncertain", "impact": "neutral", "relevance": "medium"},
                {"category": "economic", "factor": "Market conditions for " + (validated_input.business_type or "new venture"), "impact": "neutral", "relevance": "high"},
                {"category": "social", "factor": "Target demographic trends", "impact": "positive", "relevance": "medium"},
                {"category": "technological", "factor": "Technology adoption in target market", "impact": "positive", "relevance": "high"},
            ],
            "five_forces": [
                {"force": "Threat of new entrants", "assessment": "Moderate barriers to entry", "strength": "medium"},
                {"force": "Bargaining power of suppliers", "assessment": "Multiple supplier options available", "strength": "low"},
                {"force": "Bargaining power of buyers", "assessment": "Price-sensitive buyers with alternatives", "strength": "medium"},
                {"force": "Threat of substitutes", "assessment": "Existing solutions partially address the need", "strength": "medium"},
                {"force": "Industry rivalry", "assessment": "Competitive market with room for differentiation", "strength": "high"},
            ],
            "risks_opportunities": {
                "risks": ["Market timing uncertainty", "Competitive response"],
                "opportunities": ["First-mover advantage in niche", "Growing market demand"]
            },
            "market_context": f"The external environment for {validated_input.market_scope or 'this market'} presents moderate challenges and opportunities. Full analysis requires additional data points that were not available.",
            "assumptions_used": [{"statement": "LLM output was unparseable — defaults used", "confidence": "low", "source": "assumed", "source_detail": None}],
            "uncertainties": ["LLM response could not be parsed — full analysis not completed"],
            "confidence_score": "low",
            "input_tokens": 0,
            "output_tokens": 0,
        }
