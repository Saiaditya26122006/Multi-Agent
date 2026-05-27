"""
Intelligence Engine — multi-step reasoning for Phase 2 child agents.

Replaces single-shot LLM calls with a structured thinking protocol:
  Step 1: DECOMPOSE — identify key decisions and dependencies
  Step 2: PRODUCE — generate draft output based on reasoning
  Step 3: CHALLENGE — red-team the draft for weaknesses
  Step 4: REVISE — fix issues, downgrade confidence, finalize

Each step uses a separate LLM call with Sonnet for reasoning quality.
"""

import json
import logging
from typing import Optional

from agents.phase2.llm_utils import strip_markdown_json

logger = logging.getLogger(__name__)


class IntelligenceEngine:
    """Shared reasoning engine used by all child agents."""

    def __init__(self, bedrock_client, model_id: str):
        self.bedrock = bedrock_client
        self.model_id = model_id

    async def reason_and_produce(
        self,
        agent_role: str,
        input_data: dict,
        output_schema_prompt: str,
        cross_section_context: Optional[dict] = None,
        reasoning_budget: int = 3,
        learning_context: str = "",
    ) -> tuple[Optional[dict], dict, dict]:
        """
        Run the full reasoning chain.

        Args:
            agent_role: Description of what this agent does
            input_data: The input package for this section
            output_schema_prompt: The prompt describing expected JSON output
            cross_section_context: Outputs from other agents (for awareness)
            reasoning_budget: Number of reasoning steps (2=light, 3=standard, 4=deep)
            learning_context: Past failure patterns to avoid (from LearningEngine)

        Returns:
            (parsed_output, reasoning_trace, token_usage)
        """
        total_usage = {"input_tokens": 0, "output_tokens": 0}

        cross_context_str = ""
        if cross_section_context:
            summaries = []
            for sec, data in cross_section_context.items():
                if isinstance(data, dict):
                    key_fields = {k: v for k, v in data.items()
                                  if k in ("confidence_score", "opportunity_description",
                                           "competitive_strategy", "icp_hypothesis",
                                           "revenue_assumptions", "headcount_plan",
                                           "break_even_analysis", "strategic_implications")}
                    if key_fields:
                        summaries.append(f"Section {sec}: {json.dumps(key_fields, default=str)[:500]}")
            if summaries:
                cross_context_str = "\n\nOTHER SECTIONS ALREADY COMPLETED:\n" + "\n".join(summaries[:5])

        input_str = json.dumps(input_data, indent=2, default=str)[:6000]

        # Hard constraint injection — numbers that MUST be used exactly
        constraints_str = ""
        hard_constraints = input_data.get("hard_constraints", {})
        if hard_constraints:
            constraint_lines = []
            for key, info in hard_constraints.items():
                if isinstance(info, dict):
                    constraint_lines.append(f"  - {key} = {info.get('value')} (from {info.get('source', 'upstream')})")
            if constraint_lines:
                constraints_str = (
                    "\n\nBINDING CONSTRAINTS (you MUST use these exact numbers — do NOT invent your own):\n"
                    + "\n".join(constraint_lines)
                )

        # Confidence ceiling — cannot claim higher confidence than weakest upstream
        ceiling_str = ""
        confidence_ceiling = input_data.get("confidence_ceiling")
        if confidence_ceiling:
            ceiling_str = f"\n\nCONFIDENCE CEILING: Your confidence_score CANNOT exceed '{confidence_ceiling}' because your upstream inputs have that confidence level. Be honest."

        # Upstream uncertainties — things you're building on that are shaky
        uncertainties_str = ""
        upstream_uncertainties = input_data.get("upstream_uncertainties", [])
        if upstream_uncertainties:
            unc_lines = [f"  - [From Section {u.get('from_section', '?')}] {u.get('uncertainty', '')}" for u in upstream_uncertainties[:8]]
            uncertainties_str = (
                "\n\nUPSTREAM UNCERTAINTIES (your inputs have these known unknowns — acknowledge them in your output, do not build confidently on shaky ground):\n"
                + "\n".join(unc_lines)
            )

        # CEO-provided data — real facts from Alex's documents
        ceo_data_str = ""
        ceo_provided = input_data.get("ceo_provided_data", {})
        if ceo_provided:
            ceo_text = json.dumps(ceo_provided, indent=2, default=str)[:3000]
            ceo_data_str = f"\n\nCEO-PROVIDED DATA (real facts from Alex — prioritize these over inferences):\n{ceo_text}"

        learning_str = ""
        if learning_context:
            learning_str = f"\n\n{learning_context}"

        # STEP 1: DECOMPOSE
        decomposition, usage = await self._call(
            system="You are a strategic business analyst. Think step by step about the problem before solving it.",
            user=f"""You are working on a business plan. Your role: {agent_role}

INPUT DATA:
{input_str}
{cross_context_str}{constraints_str}{ceiling_str}{uncertainties_str}{ceo_data_str}{learning_str}

Before producing any output, analyze the problem:
1. What are the 3 most critical judgments I must make for this section?
2. For each judgment, what evidence do I have? What's missing?
3. What causal chains exist? (A leads to B which implies C)
4. What is the ONE assumption that, if wrong, invalidates my entire analysis?
5. What would a skeptical investor challenge first?

Think carefully. Use specific numbers from the inputs, not generalities.""",
            max_tokens=2048,
        )
        total_usage["input_tokens"] += usage.get("input_tokens", 0)
        total_usage["output_tokens"] += usage.get("output_tokens", 0)

        if not decomposition:
            return None, {}, total_usage

        # STEP 2: PRODUCE DRAFT
        draft_raw, usage = await self._call(
            system=f"You are a senior business plan writer. {agent_role}. Produce rigorous, specific, quantified analysis.",
            user=f"""Based on your strategic analysis:

{decomposition}

Now produce the structured output. Rules:
- Every number must trace to a named assumption
- Every strategic claim needs a "because" — not just assertion
- Be honest about confidence: LOW means "I'm guessing", not "it's probably fine"
- Be specific: "$120k ARR" not "significant revenue"; "18-month window" not "limited time"
- If cross-section data contradicts your conclusion, note the conflict
{constraints_str}{ceiling_str}{uncertainties_str}

INPUT DATA:
{input_str}

{output_schema_prompt}""",
            max_tokens=4096,
        )
        total_usage["input_tokens"] += usage.get("input_tokens", 0)
        total_usage["output_tokens"] += usage.get("output_tokens", 0)

        if not draft_raw:
            return None, {"decomposition": decomposition}, total_usage

        # STEP 3: CHALLENGE (skip if reasoning_budget < 3)
        challenge = ""
        if reasoning_budget >= 3:
            challenge, usage = await self._call(
                system="You are a ruthless investor doing due diligence. Find every weakness. Be specific — cite exact claims and numbers that are wrong or unsupported.",
                user=f"""Review this business plan section output. Find problems.

DRAFT OUTPUT:
{draft_raw[:4000]}

ORIGINAL REASONING:
{decomposition[:2000]}

Check for:
1. MATH ERRORS — Does A × B actually equal C? Are growth rates consistent?
2. LOGICAL GAPS — Are there unsupported leaps? Claims without evidence?
3. CONFIDENCE INFLATION — Is anything labelled "high" that should be "low"?
4. COMPETITIVE BLINDNESS — What would a competitor say about this?
5. SURVIVORSHIP BIAS — Is this assuming best-case without acknowledging failure modes?

Be brutal. An investor reading this will be. List specific problems, not vague concerns.""",
                max_tokens=2048,
            )
            total_usage["input_tokens"] += usage.get("input_tokens", 0)
            total_usage["output_tokens"] += usage.get("output_tokens", 0)

        # STEP 4: REVISE
        if challenge and reasoning_budget >= 3:
            final_raw, usage = await self._call(
                system=f"You are finalizing a business plan section. {agent_role}. Incorporate valid challenges. Respond with ONLY valid JSON.",
                user=f"""ORIGINAL DRAFT:
{draft_raw[:4000]}

CHALLENGES RAISED:
{challenge[:2000]}

Revise the output:
- Fix math errors and logical inconsistencies found in the challenge
- Downgrade confidence_score if challenges reveal genuine uncertainty
- Add valid challenges to the uncertainties list
- DO NOT water down your analysis — keep conclusions sharp and specific
- If a challenge is invalid (the draft was actually correct), ignore it

{output_schema_prompt}

Return ONLY the final valid JSON object.""",
                max_tokens=4096,
            )
            total_usage["input_tokens"] += usage.get("input_tokens", 0)
            total_usage["output_tokens"] += usage.get("output_tokens", 0)
        else:
            final_raw = draft_raw

        # Parse final output
        parsed = self._parse_output(final_raw)

        reasoning_trace = {
            "decomposition": decomposition[:2000] if decomposition else "",
            "challenge": challenge[:2000] if challenge else "",
            "revisions_applied": bool(challenge),
            "reasoning_budget": reasoning_budget,
        }

        return parsed, reasoning_trace, total_usage

    async def grade_evidence(
        self,
        claims: list,
        available_evidence: dict,
    ) -> list:
        """Evaluate whether confidence labels on assumptions are honest."""
        if not claims:
            return claims

        claims_str = json.dumps(claims[:10], indent=2, default=str)
        evidence_str = json.dumps(available_evidence, indent=2, default=str)[:3000]

        response, _ = await self._call(
            system="You are an evidence grader. Evaluate whether each claim's confidence label is honest given the available evidence. Respond with ONLY a JSON array.",
            user=f"""For each assumption below, check if the confidence and source labels are accurate.

ASSUMPTIONS:
{claims_str}

AVAILABLE EVIDENCE:
{evidence_str}

For each assumption, return:
{{"statement": "...", "original_confidence": "...", "corrected_confidence": "high|medium|low", "reason": "..."}}

If the label is already correct, keep it unchanged. Only correct inflated labels.
Return ONLY a valid JSON array.""",
            max_tokens=2048,
        )

        if response:
            try:
                text = strip_markdown_json(response)
                graded = json.loads(text)
                if isinstance(graded, list):
                    return graded
            except (json.JSONDecodeError, ValueError):
                pass

        return claims

    async def calibrate_confidence(
        self,
        section_output: dict,
        devils_advocate_result: dict,
    ) -> str:
        """Recalibrate a section's confidence score based on Devil's Advocate challenges."""
        verdict = devils_advocate_result.get("verdict", "pass")
        challenges = devils_advocate_result.get("challenges", [])
        recommended = devils_advocate_result.get("recommended_confidence", "medium")
        current = section_output.get("confidence_score", "medium")

        if verdict == "pass" and not challenges:
            return current

        high_severity = sum(1 for c in challenges if c.get("severity") == "high")
        medium_severity = sum(1 for c in challenges if c.get("severity") == "medium")

        if high_severity >= 2:
            return "low"
        if high_severity == 1 or medium_severity >= 3:
            if current == "high":
                return "medium"
            return "low"
        if medium_severity >= 1:
            if current == "high":
                return "medium"

        return recommended

    async def apply_so_what_filter(
        self,
        section_output: dict,
        agent_role: str,
    ) -> Optional[str]:
        """Ask 'So what?' — does this section actually help the CEO make a decision?

        Returns None if section passes, or a critique string if it fails.
        """
        key_fields = {k: v for k, v in section_output.items()
                      if k not in ("input_tokens", "output_tokens", "model_used", "task_id",
                                   "section_number", "reasoning_trace")}
        section_str = json.dumps(key_fields, indent=2, default=str)[:4000]

        response, _ = await self._call(
            system="You are a ruthless editor. Your only job: determine if this section output would help a CEO make a concrete business decision, or if it's just filler.",
            user=f"""Section from: {agent_role}

OUTPUT:
{section_str}

Answer these 3 questions:
1. Can a CEO take a SPECIFIC ACTION based on this output? (Name the action)
2. Are the numbers specific enough to put in a spreadsheet? (Not "significant" — actual numbers)
3. Does this tell the CEO something they couldn't guess in 10 seconds?

If ALL 3 answers are YES: respond with exactly "PASS"
If ANY answer is NO: respond with a one-sentence critique starting with "FAIL:"

Be harsh. Generic outputs like "the market is growing" or "competition exists" fail instantly.""",
            max_tokens=256,
        )

        if response and response.strip().startswith("PASS"):
            return None
        return response

    async def validate_hypotheses(self, section_output: dict, agent_role: str) -> list:
        """Test whether quantitative claims in a section are internally consistent.

        Checks funnel math, unit economics, and timeline feasibility.
        Returns a list of failed hypotheses (empty if all pass).
        """
        quantitative_fields = {}
        for key, value in section_output.items():
            if key.startswith("_") or key in ("input_tokens", "output_tokens", "model_used",
                                               "task_id", "section_number", "reasoning_trace"):
                continue
            if isinstance(value, (int, float)):
                quantitative_fields[key] = value
            elif isinstance(value, dict):
                for k, v in value.items():
                    if isinstance(v, (int, float)):
                        quantitative_fields[f"{key}.{k}"] = v

        if len(quantitative_fields) < 2:
            return []

        fields_str = json.dumps(quantitative_fields, indent=2, default=str)
        section_str = json.dumps(
            {k: v for k, v in section_output.items()
             if k not in ("input_tokens", "output_tokens", "model_used", "task_id", "reasoning_trace")},
            indent=2, default=str,
        )[:4000]

        response, _ = await self._call(
            system="You are a quantitative analyst. Your job: check if the numbers in this business plan section are internally consistent and realistic. Only flag REAL problems — not stylistic preferences.",
            user=f"""Section from: {agent_role}

NUMERICAL VALUES EXTRACTED:
{fields_str}

FULL SECTION:
{section_str}

Check these hypotheses:
1. FUNNEL MATH: If volume=X and conversion=Y%, does the required traffic/leads make sense? (e.g., 500 sales at 2% conversion = 25,000 leads needed — is that realistic for the business type?)
2. UNIT ECONOMICS: Does revenue_per_unit × volume actually equal total revenue? Does CAC × customers equal total acquisition spend?
3. TIMELINE FEASIBILITY: Can you actually achieve the claimed volume in the claimed timeframe given the stated resources?
4. GROWTH CONSISTENCY: Are year-over-year growth rates consistent with the market size and competitive position described?

For each check, answer PASS or FAIL with ONE sentence explaining why.

Return ONLY valid JSON array:
[{{"hypothesis": "funnel_math|unit_economics|timeline|growth", "result": "pass|fail", "explanation": "...", "numbers_involved": "..."}}]

If everything checks out, return an empty array: []""",
            max_tokens=1024,
        )

        if not response:
            return []

        try:
            text = strip_markdown_json(response)
            results = json.loads(text)
            if isinstance(results, list):
                return [r for r in results if isinstance(r, dict) and r.get("result") == "fail"]
        except (json.JSONDecodeError, ValueError):
            pass

        return []

    async def _call(self, system: str, user: str, max_tokens: int = 4096, retries: int = 3) -> tuple[Optional[str], dict]:
        """Make a single LLM call with exponential backoff on throttling."""
        import asyncio
        from botocore.exceptions import ReadTimeoutError, ConnectTimeoutError

        for attempt in range(retries):
            try:
                response = self.bedrock.converse(
                    modelId=self.model_id,
                    system=[{"text": system}],
                    messages=[{"role": "user", "content": [{"text": user}]}],
                    inferenceConfig={"maxTokens": max_tokens},
                )
                usage = response.get("usage", {})
                text = response["output"]["message"]["content"][0]["text"]
                return text, {
                    "input_tokens": usage.get("inputTokens", 0),
                    "output_tokens": usage.get("outputTokens", 0),
                }
            except self.bedrock.exceptions.ThrottlingException:
                wait = 2 ** attempt
                logger.warning("[IntelligenceEngine] Throttled — retrying in %ds (attempt %d/%d)", wait, attempt + 1, retries)
                await asyncio.sleep(wait)
            except (self.bedrock.exceptions.ModelTimeoutException, ReadTimeoutError, ConnectTimeoutError):
                wait = 3 ** attempt
                logger.warning("[IntelligenceEngine] Timeout — retrying in %ds (attempt %d/%d)", wait, attempt + 1, retries)
                await asyncio.sleep(wait)
            except Exception as e:
                logger.error("[IntelligenceEngine] LLM call failed: %s", e)
                return None, {}

        logger.error("[IntelligenceEngine] All %d retries exhausted", retries)
        return None, {}

    def _parse_output(self, raw: Optional[str]) -> Optional[dict]:
        """Attempt to parse JSON from raw LLM output."""
        if not raw:
            return None
        text = strip_markdown_json(raw)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
