import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional

import boto3
from spade.agent import Agent
from spade.behaviour import CyclicBehaviour, OneShotBehaviour
from spade.message import Message

from memory.redis_client import RedisClient
from agents.phase2.llm_utils import parse_json_with_retry, signal_ready
from agents.phase2.intelligence_engine import IntelligenceEngine
from schemas.inputs.marketing_strategy import MarketingStrategyInput
from schemas.outputs.marketing_strategy import MarketingStrategyOutput
from services.search_service import search_for_section

logger = logging.getLogger(__name__)

SEARCH_QUERIES = {
    "8": [
        "institutional SaaS pricing universities Europe 2025",
        "academic software procurement business schools budget",
        "research quality management tools university pricing"
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

SYSTEM_PROMPT = """You are the Marketing Strategy agent in a multi-agent business plan system.
Your role: build the full marketing plan where every number traces to a conversion assumption,
every channel choice is justified with evidence, and the budget connects directly to revenue targets.

## REASONING FRAMEWORK — Apply each lens before writing output:

1. CAC-TO-CONVERSION CHAIN (Every acquisition cost must trace to mechanics)
   - CAC is not a single number — it is: (total spend on channel) / (customers acquired from that channel).
   - For each channel, state: traffic/reach estimate, conversion rate assumption, cost per impression/click/lead, and resulting CAC.
   - If your CAC estimate does not have this chain, label confidence as "low" — you are guessing.
   - CAC must be compared to LTV. If CAC > LTV/3, flag this as a risk. If CAC > LTV, flag as FATAL.
   - sales_cycle_months must reflect the ICP's actual buying behaviour. Enterprise = 6-12 months. SMB self-serve = 0-1 months. Do not default to 3.

2. CHANNEL SELECTION (Evidence required, not vibes)
   - For each channel you recommend, answer: "Where is evidence that MY specific ICP actually responds to this channel?"
   - Evidence sources: competitor ad spend (where are they advertising?), ICP research (where do they spend time?), industry benchmarks for conversion rates in this channel.
   - NEVER recommend "content marketing" without specifying: content type, distribution mechanism, keyword strategy or audience, and time-to-payoff (content takes 6-12 months to compound).
   - NEVER recommend "social media" without naming the specific platform and why the ICP is there.
   - NEVER recommend "partnerships" without naming a specific type of partner and what they get from the deal.

3. PRICING LOGIC (Not just "what competitors charge")
   - Pricing must connect to: willingness to pay (derived from pain severity), competitive alternatives, and cost structure (must be above COGS + margin).
   - If pricing is inferred: state the reasoning chain. "Competitor X charges $Y, our product does Z more/less, therefore our price should be in range A-B."
   - volume_year1 must be derivable from: (addressable prospects) x (conversion rate) x (12 months / sales_cycle_months). If it is not, flag the inconsistency.

4. BUDGET-TO-REVENUE TRACING
   - Total marketing spend in Year 1 = (CAC x volume_year1) + brand/content investments.
   - This must be affordable given the cost structure from prior sections. If it is not, flag the gap.
   - Growth rates from Year 1 to Year 2 to Year 3 must be justified by either: increasing conversion (state why), expanding addressable market (state how), or increasing spend (state where the money comes from).

5. COMPETITIVE POSITIONING (Specific, not generic)
   - competitive_advantages must pass the "so what?" test. For each advantage, state: "This matters to the ICP because [specific pain it addresses] and competitors cannot replicate it because [specific barrier]."
   - If an advantage is easily copied (e.g., "good UX"), it is not an advantage — it is table stakes.

## ANTI-PATTERNS — If you catch yourself writing any of these, you do not have enough information:
- NEVER write "leverage social media to build brand awareness" — name the platform, content format, and audience size.
- NEVER write "word of mouth" as a channel strategy — it is an outcome, not a strategy. What triggers the word of mouth?
- NEVER write "competitive pricing" without stating the price and what it is competitive with.
- NEVER write "digital marketing" as a channel — that is a category containing 20+ channels. Name the specific ones.
- NEVER write "strategic partnerships" without naming the partner type and mutual value exchange.
- NEVER claim volume_year2 = 5x volume_year1 without explaining what changes to drive that growth.

## KILL CONDITIONS — Flag as FATAL instead of filling the template:
- If there is no ICP hypothesis and no competitive_strategy from prior sections, flag as FATAL: "Cannot build marketing strategy without knowing who to sell to or how to position."
- If CAC estimate > revenue per customer in Year 1 (i.e., unit economics are negative with no path to improvement), flag as FATAL: "Unit economics do not support customer acquisition — revise pricing or cost model."

## UNIT ECONOMICS — CRITICAL SECTION (Must calculate LTV, CAC, and ratios):

### 1. Calculate CAC (Customer Acquisition Cost):
```json
"cac": {
  "total_cac": <float>,  // Total cost to acquire one customer
  "breakdown": {
    "sales_team_cost_per_customer": <float>,  // Sales salaries / customers acquired
    "marketing_spend_per_customer": <float>,  // Ads, content, events / customers
    "tools_and_overhead": <float>  // CRM, martech, attribution tools
  },
  "validation_source": "<validated|assumed|benchmark>",
  "confidence": "high"|"medium"|"low"
}
```

### 2. Calculate LTV (Lifetime Value):
```json
"ltv": {
  "calculation_method": "avg_revenue_annual * (1 / churn_rate) * gross_margin",
  "avg_revenue_annual": <float>,  // price_per_unit from revenue_assumptions
  "churn_rate_annual": <float>,  // 0.10 = 10% churn/year (median SaaS: 10-15%)
  "customer_lifetime_years": <float>,  // 1 / churn_rate
  "gross_margin": <float>,  // 0.70-0.85 for SaaS (after COGS)
  "ltv_gross": <float>,  // avg_revenue * lifetime_years (before margin)
  "ltv_net": <float>  // ltv_gross * gross_margin (after COGS)
}
```

### 3. Calculate Key Ratios:
```json
"ltv_cac_ratio": <float>,  // ltv_net / total_cac
"payback_period_months": <float>,  // (total_cac / (avg_revenue_annual * gross_margin)) * 12
"health_assessment": "<assessment>",  // See rules below
"key_assumptions": [
  "Churn rate assumed at <X>% based on <source>",
  "Gross margin <Y>% based on <source>",
  "CAC based on <source>"
],
"uncertainties": [
  "No retention data — churn rate is assumed",
  "CAC not validated with pilot sales data",
  ...
]
```

### 🚨 MAGIC RATIO GUARDRAIL — ENFORCED (LTV:CAC >= 3.0)

**CRITICAL RULE**: If `ltv_cac_ratio < 3.0`, you **CANNOT PROCEED** to quality review.

You have 3 options:
1. **Increase pricing** → raises LTV
2. **Reduce CAC** → pick cheaper channels, improve conversion
3. **Increase retention** (lower churn) → extends customer lifetime, raises LTV
4. **Justify exception** → some business models accept < 3:1

**Acceptable exceptions** (must provide written justification):
- **Marketplace/Platform**: Network effects improve unit economics over time (Uber, Airbnb started <3:1)
- **Land-and-expand**: Enterprise SaaS where initial deal is small but expansion revenue is high
- **VC-funded land grab**: Intentional negative unit economics to capture market share (must cite funding round)
- **E-commerce with repeat purchases**: Initial purchase <3:1 but repeat rate >50% brings blended LTV:CAC above 3:1 by Year 2

**Unacceptable justifications**:
- "We'll figure it out later"
- "We'll optimize once we scale"
- "Competitors also have low ratios" (cite proof)
- "Our product is better so customers will stay longer" (cite retention data)

**How to set magic_ratio_pass**:
```json
"unit_economics": {
  "ltv_cac_ratio": 2.5,
  "magic_ratio_pass": false,
  "magic_ratio_justification": "Land-and-expand model: initial deal averages $5K/year (LTV $15K), but 70% of customers expand to $20K/year by Year 2 (validated by competitor Gong's S-1). Blended LTV:CAC by Year 2 = 4.2:1.",
  ...
}
```

If `ltv_cac_ratio >= 3.0`:
```json
"magic_ratio_pass": true,
"magic_ratio_justification": null
```

If `ltv_cac_ratio < 3.0` AND you have NO valid justification:
- You MUST escalate immediately — do NOT fill the rest of the template
- The agent will automatically escalate to CEO for decision

### Unit Economics Health Rules:
- **LTV:CAC >= 5:1** → "excellent — strong unit economics, capital efficient growth"
- **LTV:CAC 3:1 to 5:1** → "healthy — viable SaaS business, acceptable efficiency"
- **LTV:CAC 1:1 to 3:1** → "WARNING — low efficiency, requires justification or optimization"
- **LTV:CAC < 1:1** → "FATAL — unit economics broken, business not viable"
- **Payback < 12 months** → "excellent cash efficiency"
- **Payback 12-18 months** → "acceptable for SaaS"
- **Payback > 18 months** → "WARNING — high capital requirement to scale"

### Validation Rules:
- If churn_rate is assumed (no retention data), confidence MUST be "low" or "medium" at best
- If CAC is from benchmarks (not actual sales), confidence MUST be "low" or "medium"
- If gross_margin is assumed, state source (SaaS average, competitor proxy, etc.)
- NEVER claim high confidence on LTV without 6+ months of retention data

## Rules:
- competitors list must have at least 2 entries with specific names (not "Competitor A")
- competitive_advantages must have at least 2 entries that pass the "so what?" test
- revenue_assumptions must include: price_per_unit, volume_year1, volume_year2, volume_year3, sales_cycle_months
- cac_assumptions must include: cac_estimate, cac_source, confidence
- unit_economics must include: cac, ltv, ltv_cac_ratio, payback_period_months, health_assessment
- market_entry_strategy must be at least 50 characters describing the specific GTM sequence
- If pricing data is unavailable from CEO, infer from competitive analysis and label as agent_inferred
- Never claim "no competitors" — always identify substitutes at minimum

You must respond with ONLY a valid JSON object. No markdown, no code blocks, no explanations before or after the JSON. The JSON must contain exactly these fields: section_number, target_market_analysis, competitors, competitive_advantages, marketing_mix, customer_relations, revenue_assumptions, cac_assumptions, unit_economics, market_entry_strategy, assumptions_used, uncertainties, confidence_score, input_tokens, output_tokens.
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


class MarketingStrategyAgent(Agent):

    def __init__(self, jid: str, password: str):
        super().__init__(jid, password)
        self.redis = RedisClient()
        self.model_id = os.getenv("CLAUDE_SONNET_MODEL", "claude-sonnet-4-20250514")
        self.bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"))
        self.intelligence = IntelligenceEngine(self.bedrock, self.model_id)

    async def setup(self):
        logger.info("[MarketingStrategy] Starting")
        self.add_behaviour(ListenBehaviour())
        signal_ready(self.redis, "marketing_strategy")

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
            validated_input = MarketingStrategyInput(
                task_id=task_id,
                session_id=session_id,
                swot_matrix=input_package.get("swot_matrix", {}),
                icp_hypothesis=input_package.get("icp_hypothesis", {}),
                competitive_strategy=input_package.get("competitive_strategy", ""),
                market_context=input_package.get("market_context", ""),
                strategic_implications=input_package.get("strategic_implications", ""),
                pricing_assumption=input_package.get("pricing_assumption"),
                target_volume=input_package.get("target_volume"),
                cac_assumptions=input_package.get("cac_assumptions"),
                partnership_targets=input_package.get("partnership_targets"),
                acceptance_criteria=task.get("acceptance_criteria", ""),
            )
        except Exception as e:
            logger.error("[MarketingStrategy] Input validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "unclear_input", str(e))
            return

        cross_context = input_package.get("cross_section_context", {})
        learning_context = input_package.get("learning_context", "")

        revision_required = input_package.get("revision_required", False)
        revision_feedback = input_package.get("revision_feedback", "")
        if revision_required and revision_feedback:
            learning_context += f"\n\nMANDATORY REVISIONS (from quality review):\n{revision_feedback}\nFix these issues. Do NOT weaken your analysis — make it more rigorous."

        input_data = {
            "swot_matrix": input_package.get("swot_matrix", {}),
            "icp_hypothesis": input_package.get("icp_hypothesis", {}),
            "competitive_strategy": input_package.get("competitive_strategy", ""),
            "market_context": input_package.get("market_context", ""),
            "strategic_implications": input_package.get("strategic_implications", ""),
            "pricing_assumption": input_package.get("pricing_assumption"),
            "target_volume": input_package.get("target_volume"),
            "cac_assumptions": input_package.get("cac_assumptions"),
            "live_market_data": _get_live_market_data("8"),
        }

        parsed, reasoning_trace, token_usage = await self.intelligence.reason_and_produce(
            agent_role=(
                "Marketing Strategy — you build the full marketing plan including target market, "
                "competitive positioning, marketing mix, revenue assumptions, and CAC estimates"
            ),
            input_data=input_data,
            output_schema_prompt=self._build_schema_prompt(),
            cross_section_context=cross_context if cross_context else None,
            reasoning_budget=4 if revision_required else 3,
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
            validated_output = MarketingStrategyOutput(**parsed)
        except Exception as e:
            logger.error("[MarketingStrategy] Output validation failed: %s", e)
            await self._escalate(task_id, session_id, pipeline_run_id, "output_conflict", str(e))
            return

        # 🚨 MAGIC RATIO GUARDRAIL — Enforce LTV:CAC >= 3.0
        unit_economics = validated_output.unit_economics
        ltv_cac_ratio = unit_economics.ltv_cac_ratio
        magic_ratio_pass = unit_economics.magic_ratio_pass
        justification = unit_economics.magic_ratio_justification

        if ltv_cac_ratio < 3.0 and not magic_ratio_pass:
            # HARD STOP — escalate to CEO
            logger.error(
                "[MarketingStrategy] MAGIC RATIO FAILURE — LTV:CAC %.2f < 3.0, no valid justification",
                ltv_cac_ratio
            )
            await self._escalate(
                task_id, session_id, pipeline_run_id,
                trigger="unit_economics_failure",
                notes=f"LTV:CAC ratio is {ltv_cac_ratio:.2f} (below 3:1 threshold). "
                      f"Options: (1) Increase pricing → raises LTV, (2) Reduce CAC via cheaper channels, "
                      f"(3) Increase retention (lower churn) → extends customer lifetime, "
                      f"(4) Provide valid justification (marketplace network effects, land-and-expand, VC-funded land grab)."
            )
            return

        if ltv_cac_ratio < 3.0 and magic_ratio_pass:
            # Exception granted — log justification
            logger.warning(
                "[MarketingStrategy] Magic ratio exception granted — LTV:CAC %.2f < 3.0 with justification: %s",
                ltv_cac_ratio, justification[:100]
            )

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
            "icp_hypothesis": original_output.get("icp_hypothesis", ""),
            "competitive_strategy": original_output.get("competitive_strategy", ""),
            "revenue_assumptions": original_output.get("revenue_assumptions", {}),
            "revision_required": True,
            "revision_feedback": f"COUNCIL REVIEW FEEDBACK:\n{revision_instructions}\n\nSPECIFIC CRITIQUES:\n{critique_text}",
            "cross_section_context": content.get("cross_section_context", {}),
        }

        revised_content = {"task": {"input_package": input_package, "task_id": task_id}}
        await self.handle_request(task_id, session_id, pipeline_run_id, revised_content)

    async def handle_propose(self, task_id, session_id, pipeline_run_id, sender, content):
        proposal = content.get("proposal", "")
        field = content.get("field", "")
        if field in ("revenue_assumptions", "cac_assumptions"):
            mother_jid = os.getenv("MOTHER_AGENT_JID", "")
            msg = Message(to=mother_jid)
            msg.set_metadata("performative", "refuse")
            msg.set_metadata("task_id", task_id)
            msg.set_metadata("session_id", session_id)
            msg.set_metadata("pipeline_run_id", pipeline_run_id)
            msg.body = json.dumps({
                "original_proposer": sender,
                "reason": "Revenue/CAC assumptions are derived from market analysis — cannot accept external override without evidence",
            })
            await self._send_msg(msg)
        else:
            mother_jid = os.getenv("MOTHER_AGENT_JID", "")
            msg = Message(to=mother_jid)
            msg.set_metadata("performative", "inform")
            msg.set_metadata("task_id", task_id)
            msg.set_metadata("session_id", session_id)
            msg.set_metadata("pipeline_run_id", pipeline_run_id)
            msg.body = json.dumps({"status": "accepted", "proposal": proposal})
            await self._send_msg(msg)

    def _build_schema_prompt(self) -> str:
        return """Return ONLY valid JSON with these exact keys:
- section_number: "8"
- target_market_analysis: {"segmentation": str, "icp_refined": str, "market_size_tam_sam_som": str}
- competitors: list of {"name": str, "positioning": str, "pricing": str|null, "strengths": [str], "weaknesses": [str]} (min 2)
- competitive_advantages: [str] (min 2, specific not generic)
- marketing_mix: {"product": str, "pricing_policy": str, "distribution": str, "promotion": str}
- customer_relations: {"communication": str, "loyalty_strategy": str}
- revenue_assumptions: {"price_per_unit": float, "volume_year1": int, "volume_year2": int, "volume_year3": int, "sales_cycle_months": int}
- cac_assumptions: {"cac_estimate": float, "cac_source": str, "confidence": "high"|"medium"|"low"}
- market_entry_strategy: str (min 50 chars — specific go-to-market plan)
- assumptions_used: list of {"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0"""

    def _build_prompt(self, inp: MarketingStrategyInput) -> str:
        return f"""Build a complete marketing strategy for this business.

SWOT MATRIX: {json.dumps(inp.swot_matrix, indent=2)}
ICP HYPOTHESIS: {json.dumps(inp.icp_hypothesis, indent=2)}
COMPETITIVE STRATEGY: {inp.competitive_strategy}
MARKET CONTEXT: {inp.market_context}
STRATEGIC IMPLICATIONS: {inp.strategic_implications}
PRICING ASSUMPTION (from CEO): {inp.pricing_assumption or 'Not provided — infer from competitors'}
TARGET VOLUME (from CEO): {inp.target_volume or 'Not provided — derive from market size'}
CAC ASSUMPTION (from CEO): {inp.cac_assumptions or 'Not provided — benchmark from industry'}
PARTNERSHIP TARGETS: {json.dumps(inp.partnership_targets) if inp.partnership_targets else 'None specified'}

Return ONLY valid JSON with these exact keys:
- section_number: "8"
- target_market_analysis: {{"segmentation": str, "icp_refined": str, "market_size_tam_sam_som": str}}
- competitors: list of {{"name": str, "positioning": str, "pricing": str|null, "strengths": [str], "weaknesses": [str]}} (min 2)
- competitive_advantages: [str] (min 2)
- marketing_mix: {{"product": str, "pricing_policy": str, "distribution": str, "promotion": str}}
- customer_relations: {{"communication": str, "loyalty_strategy": str}}
- revenue_assumptions: {{"price_per_unit": float, "volume_year1": int, "volume_year2": int, "volume_year3": int, "sales_cycle_months": int}}
- cac_assumptions: {{"cac_estimate": float, "cac_source": str, "confidence": "high"|"medium"|"low"}}
- market_entry_strategy: str (min 50 chars)
- assumptions_used: list of {{"statement": str, "confidence": "high"|"medium"|"low", "source": "validated"|"alex_provided"|"agent_inferred"|"assumed", "source_detail": str|null}}
- uncertainties: [str]
- confidence_score: "high"|"medium"|"low"
- input_tokens: 0
- output_tokens: 0
"""

    def _parse_llm_response(self, raw: str, inp: MarketingStrategyInput) -> dict:
        """Parse LLM response with retry before falling back to defaults."""
        result = parse_json_with_retry(
            raw=raw,
            bedrock_client=self.bedrock,
            model_id=self.model_id,
            system_prompt=SYSTEM_PROMPT,
            user_message=self._build_prompt(inp),
            agent_name="MarketingStrategy",
            max_tokens=8192,
        )
        if result is not None:
            return result

        logger.warning("[MarketingStrategy] Both parse attempts failed, constructing fallback")

        # Calculate fallback unit economics
        price = 100.0
        churn = 0.12
        gross_margin = 0.80
        cac = 500.0

        lifetime_years = 1 / churn  # 8.33 years
        ltv_gross = price * lifetime_years  # 833
        ltv_net = ltv_gross * gross_margin  # 666
        ltv_cac = ltv_net / cac  # 1.33
        payback_months = (cac / (price * gross_margin)) * 12  # 7.5 months

        return {
                "section_number": "8",
                "target_market_analysis": {"segmentation": "To be determined based on ICP validation", "icp_refined": "Initial hypothesis requires market testing", "market_size_tam_sam_som": "Requires further research"},
                "competitors": [
                    {"name": "Incumbent Solution A", "positioning": "Established market player", "pricing": None, "strengths": ["Brand recognition", "Existing customer base"], "weaknesses": ["Slow innovation", "Legacy technology"]},
                    {"name": "Alternative/Substitute B", "positioning": "Adjacent market solution", "pricing": None, "strengths": ["Low cost"], "weaknesses": ["Poor fit for target use case"]},
                ],
                "competitive_advantages": ["Novel approach to customer problem", "Speed and agility as early-stage venture"],
                "marketing_mix": {"product": "Core product addressing identified pain points", "pricing_policy": "Value-based pricing aligned with market", "distribution": "Direct-to-customer digital channels", "promotion": "Content marketing and targeted outreach"},
                "customer_relations": {"communication": "Direct engagement via digital channels", "loyalty_strategy": "Early adopter program with feedback loop"},
                "revenue_assumptions": {"price_per_unit": price, "volume_year1": 100, "volume_year2": 500, "volume_year3": 1500, "sales_cycle_months": 3},
                "cac_assumptions": {"cac_estimate": cac, "cac_source": "Industry benchmark — not validated", "confidence": "low"},
                "unit_economics": {
                    "cac": {
                        "total_cac": cac,
                        "breakdown": {
                            "sales_team_cost_per_customer": 300.0,
                            "marketing_spend_per_customer": 150.0,
                            "tools_and_overhead": 50.0
                        },
                        "validation_source": "assumed — fallback defaults",
                        "confidence": "low"
                    },
                    "ltv": {
                        "calculation_method": "avg_revenue_annual * (1 / churn_rate) * gross_margin",
                        "avg_revenue_annual": price,
                        "churn_rate_annual": churn,
                        "customer_lifetime_years": round(lifetime_years, 2),
                        "gross_margin": gross_margin,
                        "ltv_gross": round(ltv_gross, 2),
                        "ltv_net": round(ltv_net, 2)
                    },
                    "ltv_cac_ratio": round(ltv_cac, 2),
                    "payback_period_months": round(payback_months, 2),
                    "health_assessment": "WARNING — LTV:CAC < 3:1 (fallback defaults, low confidence)",
                    "key_assumptions": [
                        "Churn rate assumed at 12% (SaaS median)",
                        "Gross margin 80% (SaaS benchmark)",
                        "CAC from industry benchmarks"
                    ],
                    "uncertainties": [
                        "No retention data — churn rate is pure assumption",
                        "CAC not validated with actual sales data",
                        "Gross margin not verified against actual cost structure"
                    ]
                },
                "market_entry_strategy": "Focus on early adopter segment with direct sales approach, then expand through referrals and content marketing",
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
            logger.error("[MarketingStrategy] LLM call failed: %s", e)
            return None, {}

    async def _escalate(self, task_id, session_id, pipeline_run_id, trigger, notes):
        mother_jid = os.getenv("MOTHER_AGENT_JID", "")
        msg = Message(to=mother_jid)
        msg.set_metadata("performative", "escalate")
        msg.set_metadata("task_id", task_id)
        msg.set_metadata("session_id", session_id)
        msg.set_metadata("pipeline_run_id", pipeline_run_id)
        msg.body = json.dumps({"trigger": trigger, "notes": notes, "section": "8", "gap_key": "pricing_assumption"})
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
            "section_number": "8",
            "agent_name": "marketing_strategy",
        })
        await self._send_msg(msg)


async def main():
    from dotenv import load_dotenv
    load_dotenv()
    jid = os.getenv("MARKETING_STRATEGY_JID")
    password = os.getenv("MARKETING_STRATEGY_PASSWORD")
    if not jid or not password:
        raise ValueError("MARKETING_STRATEGY_JID and PASSWORD must be set")
    agent = MarketingStrategyAgent(jid=jid, password=password)
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
