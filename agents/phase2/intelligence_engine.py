"""
Intelligence Engine — multi-step reasoning for Phase 2 child agents.

Enforced reasoning chain with programmatic validation between steps:
  Step 1: DECOMPOSE — extract structured judgments with evidence requirements
  Step 2: PRODUCE — generate draft, verify all judgments are addressed
  Step 3: CHALLENGE — structured critique with typed problems
  Step 4: REVISE — fix with explicit checklist, verify resolution

Each step validates the output of the previous step before proceeding.
"""

import json
import logging
import re
from typing import Optional

from agents.phase2.llm_utils import strip_markdown_json

logger = logging.getLogger(__name__)

CAUSAL_MARKERS = [
    "because", "therefore", "since", "given that", "implies",
    "leads to", "results in", "causes", "driven by", "due to",
    "as a result", "consequently", "which means",
]

GENERIC_PHRASES = [
    "unique value proposition", "first-mover advantage",
    "differentiation through", "innovative approach",
    "cutting-edge", "best-in-class", "world-class",
    "leveraging synergies", "holistic approach",
    "comprehensive solution", "industry-leading",
    "scalable platform", "end-to-end solution",
    "robust framework", "paradigm shift",
]


class IntelligenceEngine:
    """Enforced reasoning engine — validates constraints between every step."""

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
        """Run the enforced reasoning chain with validation between steps."""
        total_usage = {"input_tokens": 0, "output_tokens": 0}

        context = self._build_context(
            input_data, cross_section_context, learning_context
        )
        input_str = json.dumps(input_data, indent=2, default=str)[:6000]

        # STEP 1: DECOMPOSE — extract structured judgments
        decomposition, judgments, usage = await self._decompose(
            agent_role, input_str, context
        )
        self._accumulate_usage(total_usage, usage)
        if not decomposition:
            return None, {}, total_usage

        # STEP 2: PRODUCE — generate draft with judgment coverage enforcement
        draft_raw, usage = await self._produce(
            agent_role, input_str, decomposition, judgments,
            output_schema_prompt, context
        )
        self._accumulate_usage(total_usage, usage)
        if not draft_raw:
            return None, {"decomposition": decomposition}, total_usage

        # ENFORCEMENT: Verify draft addresses all judgments
        coverage = self._check_judgment_coverage(draft_raw, judgments)
        if coverage["missing"]:
            logger.info(
                "[IE] Draft missing %d judgments — re-producing with gaps",
                len(coverage["missing"]),
            )
            draft_raw, usage = await self._produce_with_gaps(
                agent_role, draft_raw, coverage["missing"],
                output_schema_prompt, context
            )
            self._accumulate_usage(total_usage, usage)

        # STEP 3: CHALLENGE — structured critique (skip if budget < 3)
        challenges = []
        challenge_raw = ""
        if reasoning_budget >= 3:
            challenge_raw, challenges, usage = await self._challenge(
                draft_raw, decomposition, context
            )
            self._accumulate_usage(total_usage, usage)

        # STEP 4: REVISE with explicit checklist
        revision_count = 0
        final_raw = draft_raw
        unresolved = []

        if challenges and reasoning_budget >= 3:
            final_raw, usage = await self._revise(
                agent_role, draft_raw, challenges, output_schema_prompt
            )
            self._accumulate_usage(total_usage, usage)
            revision_count = 1

            # ENFORCEMENT: Verify each challenge was addressed
            unresolved = self._check_challenge_resolution(final_raw, challenges)
            if unresolved and reasoning_budget >= 4:
                logger.info(
                    "[IE] %d challenges unresolved — second revision pass",
                    len(unresolved),
                )
                final_raw, usage = await self._revise_targeted(
                    agent_role, final_raw, unresolved, output_schema_prompt
                )
                self._accumulate_usage(total_usage, usage)
                revision_count = 2
                unresolved = self._check_challenge_resolution(
                    final_raw, challenges
                )

        # Parse final output
        parsed = self._parse_output(final_raw)
        if parsed is None and final_raw != draft_raw:
            logger.warning("[IE] REVISE unparseable — falling back to draft")
            parsed = self._parse_output(draft_raw)

        if parsed:
            parsed = self._normalize_confidence(parsed)

        # ENFORCEMENT: Force confidence downgrade if unresolved challenges remain
        if parsed and unresolved:
            parsed["confidence_score"] = "low"
            parsed["_unresolved_challenges"] = [
                c.get("problem", str(c)) for c in unresolved[:3]
            ]

        # ENFORCEMENT: Detect generic filler in output
        if parsed:
            generic_count = self._count_generic_phrases(parsed)
            if generic_count >= 3:
                parsed["confidence_score"] = "low"
                parsed.setdefault("_quality_warnings", []).append(
                    f"Output contains {generic_count} generic phrases — "
                    "may lack specificity"
                )

        reasoning_trace = {
            "decomposition": decomposition[:2000] if decomposition else "",
            "judgments_extracted": len(judgments),
            "judgments_covered": coverage.get("covered", 0),
            "judgments_missing": len(coverage.get("missing", [])),
            "challenge": challenge_raw[:2000] if challenge_raw else "",
            "challenges_found": len(challenges),
            "challenges_resolved": len(challenges) - len(unresolved),
            "challenges_unresolved": len(unresolved),
            "revision_count": revision_count,
            "revisions_applied": revision_count > 0,
            "reasoning_budget": reasoning_budget,
            "generic_phrase_count": self._count_generic_phrases(parsed) if parsed else 0,
        }

        return parsed, reasoning_trace, total_usage

    # ── Step implementations ─────────────────────────────────────────────────

    async def _decompose(
        self, agent_role: str, input_str: str, context: dict
    ) -> tuple[Optional[str], list, dict]:
        """Step 1: Extract structured judgments from analysis."""
        ctx = self._format_context(context)
        decomposition, usage = await self._call(
            system=(
                "You are a strategic business analyst. Analyze the problem "
                "and extract STRUCTURED JUDGMENTS. Each judgment must name "
                "a specific claim, the evidence for/against, and what would "
                "change your mind."
            ),
            user=f"""Your role: {agent_role}

INPUT DATA:
{input_str}
{ctx}

Analyze this problem by extracting STRUCTURED JUDGMENTS. For each:

FORMAT YOUR RESPONSE AS:

JUDGMENT 1: [specific claim you must evaluate]
EVIDENCE FOR: [what supports this]
EVIDENCE AGAINST: [what undermines this]
CONFIDENCE: [high/medium/low]
KILL CONDITION: [what fact, if true, would invalidate this judgment]

JUDGMENT 2: ...
(continue for 3-5 judgments)

CAUSAL CHAIN: [A] → [B] → [C] (trace the logic)

FATAL ASSUMPTION: The ONE thing that kills this entire analysis if wrong.

Be specific. Use numbers from the input. Never write "significant" — write the number.""",
            max_tokens=2048,
        )

        judgments = self._parse_judgments(decomposition) if decomposition else []
        return decomposition, judgments, usage

    async def _produce(
        self,
        agent_role: str,
        input_str: str,
        decomposition: str,
        judgments: list,
        schema_prompt: str,
        context: dict,
    ) -> tuple[Optional[str], dict]:
        """Step 2: Generate draft that must address all judgments."""
        judgment_checklist = "\n".join(
            f"  - JUDGMENT {i+1}: {j.get('claim', j.get('text', ''))}"
            for i, j in enumerate(judgments)
        )
        ctx = self._format_context(context)

        draft_raw, usage = await self._call(
            system=(
                f"You are a senior business plan writer. {agent_role}. "
                "Produce rigorous, specific, quantified analysis. "
                "Every claim needs a 'because'. Every number needs a source. "
                "Be concise — short values in JSON fields. No essays."
            ),
            user=f"""Based on your strategic analysis:

{decomposition[:3000]}

YOUR OUTPUT MUST ADDRESS EACH OF THESE JUDGMENTS:
{judgment_checklist}

Rules:
- Every number must trace to a named assumption or input data
- Every strategic claim needs a "because" with specific evidence
- Confidence: LOW = "I'm guessing", MEDIUM = "reasonable inference", HIGH = "data-backed"
- Be specific: "$120k ARR" not "significant revenue"
- If cross-section data contradicts your conclusion, note the conflict
- NEVER use phrases like "unique value proposition" or "first-mover advantage" — say what specifically is unique and why it matters
- Keep string field values under 300 characters
{ctx}

INPUT DATA:
{input_str}

{schema_prompt}""",
            max_tokens=8192,
        )
        return draft_raw, usage

    async def _produce_with_gaps(
        self,
        agent_role: str,
        draft_raw: str,
        missing_judgments: list,
        schema_prompt: str,
        context: dict,
    ) -> tuple[Optional[str], dict]:
        """Re-produce draft addressing specific missed judgments."""
        gap_list = "\n".join(
            f"  - {j.get('claim', j.get('text', ''))}" for j in missing_judgments
        )
        ctx = self._format_context(context)

        result, usage = await self._call(
            system=(
                f"You are revising a business plan draft. {agent_role}. "
                "Your previous draft missed critical judgments. Fix it."
            ),
            user=f"""Your draft output:
{draft_raw[:5000]}

MISSING — your draft did NOT address these critical judgments:
{gap_list}

Revise the JSON to explicitly address each missing judgment.
Do not weaken existing analysis — ADD the missing reasoning.
{ctx}

{schema_prompt}

Return ONLY valid JSON. Start with {{ end with }}.""",
            max_tokens=8192,
        )
        return result or draft_raw, usage

    async def _challenge(
        self, draft_raw: str, decomposition: str, context: dict
    ) -> tuple[str, list, dict]:
        """Step 3: Structured critique returning typed problems."""
        challenge_raw, usage = await self._call(
            system=(
                "You are a ruthless investor doing due diligence. "
                "Find every weakness. Return STRUCTURED problems."
            ),
            user=f"""Review this business plan section. Find SPECIFIC problems.

DRAFT OUTPUT:
{draft_raw[:4000]}

ORIGINAL REASONING:
{decomposition[:2000]}

For each problem found, use this format:

PROBLEM 1:
TYPE: [math_error | logical_gap | confidence_inflation | competitive_blindness | unsupported_claim | generic_filler]
LOCATION: [which field or claim]
WHAT'S WRONG: [specific issue with numbers]
FIX NEEDED: [what the revision must do]

PROBLEM 2: ...

Only report REAL problems with specific evidence. Not stylistic preferences.
If the draft is solid, say "NO PROBLEMS FOUND".""",
            max_tokens=2048,
        )

        challenges = self._parse_challenges(challenge_raw) if challenge_raw else []
        return challenge_raw or "", challenges, usage

    async def _revise(
        self,
        agent_role: str,
        draft_raw: str,
        challenges: list,
        schema_prompt: str,
    ) -> tuple[Optional[str], dict]:
        """Step 4: Revise with explicit checklist of problems to fix."""
        checklist = "\n".join(
            f"  [{i+1}] ({c.get('type', 'issue')}) {c.get('problem', '')} "
            f"→ FIX: {c.get('fix', 'address this')}"
            for i, c in enumerate(challenges)
        )

        final_raw, usage = await self._call(
            system=(
                f"You are finalizing a business plan section. {agent_role}. "
                "Fix the problems below. Respond with ONLY valid JSON."
            ),
            user=f"""DRAFT:
{draft_raw[:5000]}

FIX THESE PROBLEMS (each MUST be addressed in your revision):
{checklist}

Rules:
- Fix math errors and logical inconsistencies
- Downgrade confidence_score if problems reveal genuine uncertainty
- Add valid problems to uncertainties list
- DO NOT water down analysis — keep conclusions sharp
- If a problem is invalid, ignore it but keep the draft claim
- Keep string values under 300 chars

{schema_prompt}

Return ONLY the final valid JSON. Start with {{ end with }}.""",
            max_tokens=8192,
        )
        return final_raw or draft_raw, usage

    async def _revise_targeted(
        self,
        agent_role: str,
        current_raw: str,
        unresolved: list,
        schema_prompt: str,
    ) -> tuple[Optional[str], dict]:
        """Second revision pass targeting only unresolved challenges."""
        issues = "\n".join(
            f"  - ({c.get('type', 'issue')}) {c.get('problem', '')}"
            for c in unresolved
        )

        result, usage = await self._call(
            system=(
                f"You are making a FINAL revision. {agent_role}. "
                "These specific issues were NOT fixed in the first revision. "
                "Fix them NOW or explicitly acknowledge them as limitations."
            ),
            user=f"""CURRENT OUTPUT (first revision already applied):
{current_raw[:5000]}

STILL UNRESOLVED — these were NOT fixed:
{issues}

Either fix each one OR add it to uncertainties with confidence="low".
Do not ignore them.

{schema_prompt}

Return ONLY valid JSON. Start with {{ end with }}.""",
            max_tokens=8192,
        )
        return result or current_raw, usage

    # ── Enforcement helpers ──────────────────────────────────────────────────

    def _parse_judgments(self, decomposition: str) -> list:
        """Extract structured judgments from decomposition text."""
        if not decomposition:
            return []

        judgments = []
        current = {}
        for line in decomposition.split("\n"):
            line_stripped = line.strip()
            upper = line_stripped.upper()
            if upper.startswith("JUDGMENT") and ":" in line_stripped:
                if current:
                    judgments.append(current)
                claim = line_stripped.split(":", 1)[1].strip()
                current = {"claim": claim, "text": claim}
            elif upper.startswith("EVIDENCE FOR:"):
                current["evidence_for"] = line_stripped.split(":", 1)[1].strip()
            elif upper.startswith("EVIDENCE AGAINST:"):
                current["evidence_against"] = line_stripped.split(":", 1)[1].strip()
            elif upper.startswith("CONFIDENCE:"):
                current["confidence"] = line_stripped.split(":", 1)[1].strip().lower()
            elif upper.startswith("KILL CONDITION:"):
                current["kill_condition"] = line_stripped.split(":", 1)[1].strip()
            elif upper.startswith("FATAL ASSUMPTION:"):
                if current:
                    judgments.append(current)
                judgments.append({
                    "claim": line_stripped.split(":", 1)[1].strip(),
                    "text": line_stripped.split(":", 1)[1].strip(),
                    "is_fatal": True,
                })
                current = {}

        if current:
            judgments.append(current)

        if not judgments:
            sentences = [
                s.strip() for s in decomposition.split(".")
                if len(s.strip()) > 20
            ]
            judgments = [{"claim": s, "text": s} for s in sentences[:4]]

        return judgments

    def _check_judgment_coverage(self, draft_raw: str, judgments: list) -> dict:
        """Check which judgments are addressed in the draft."""
        if not judgments or not draft_raw:
            return {"covered": 0, "missing": [], "total": len(judgments)}

        draft_lower = draft_raw.lower()
        covered = 0
        missing = []

        for j in judgments:
            claim = j.get("claim", j.get("text", ""))
            keywords = [
                w for w in claim.lower().split()
                if len(w) > 4 and w not in ("should", "would", "could", "their", "which", "about", "these")
            ]
            matches = sum(1 for kw in keywords if kw in draft_lower)
            if keywords and matches / len(keywords) >= 0.4:
                covered += 1
            else:
                missing.append(j)

        return {"covered": covered, "missing": missing, "total": len(judgments)}

    def _parse_challenges(self, challenge_raw: str) -> list:
        """Extract structured challenges from critique text."""
        if not challenge_raw:
            return []

        if "NO PROBLEMS FOUND" in challenge_raw.upper():
            return []

        challenges = []
        current = {}
        for line in challenge_raw.split("\n"):
            line_stripped = line.strip()
            upper = line_stripped.upper()
            if upper.startswith("PROBLEM") and ":" in line_stripped:
                if current and current.get("problem"):
                    challenges.append(current)
                current = {}
            elif upper.startswith("TYPE:"):
                current["type"] = line_stripped.split(":", 1)[1].strip().lower()
            elif upper.startswith("LOCATION:"):
                current["location"] = line_stripped.split(":", 1)[1].strip()
            elif upper.startswith("WHAT'S WRONG:") or upper.startswith("WHATS WRONG:"):
                current["problem"] = line_stripped.split(":", 1)[1].strip()
            elif upper.startswith("FIX NEEDED:"):
                current["fix"] = line_stripped.split(":", 1)[1].strip()

        if current and current.get("problem"):
            challenges.append(current)

        if not challenges and len(challenge_raw) > 50:
            paragraphs = [
                p.strip() for p in challenge_raw.split("\n\n")
                if len(p.strip()) > 30
            ]
            challenges = [
                {"type": "unstructured", "problem": p[:200], "fix": "address this"}
                for p in paragraphs[:5]
            ]

        return challenges

    def _check_challenge_resolution(self, revised_raw: str, challenges: list) -> list:
        """Check which challenges were NOT addressed in the revision."""
        if not challenges or not revised_raw:
            return []

        revised_lower = revised_raw.lower()
        unresolved = []

        for c in challenges:
            location = c.get("location", "").lower()
            problem = c.get("problem", "").lower()

            keywords = [
                w for w in (location + " " + problem).split()
                if len(w) > 4
            ][:6]

            if not keywords:
                continue

            matches = sum(1 for kw in keywords if kw in revised_lower)
            if keywords and matches / len(keywords) < 0.3:
                unresolved.append(c)

        return unresolved

    def _count_generic_phrases(self, output: dict) -> int:
        """Count generic filler phrases in output text fields."""
        if not output:
            return 0

        text = json.dumps(output, default=str).lower()
        return sum(1 for phrase in GENERIC_PHRASES if phrase in text)

    # ── Context building ─────────────────────────────────────────────────────

    def _build_context(
        self,
        input_data: dict,
        cross_section_context: Optional[dict],
        learning_context: str,
    ) -> dict:
        """Build all context strings for injection into prompts."""
        ctx = {
            "cross_section": "",
            "constraints": "",
            "ceiling": "",
            "uncertainties": "",
            "ceo_data": "",
            "live_data": "",
            "learning": "",
        }

        if cross_section_context:
            summaries = []
            for sec, data in cross_section_context.items():
                if isinstance(data, dict):
                    key_fields = {
                        k: v for k, v in data.items()
                        if k in (
                            "confidence_score", "opportunity_description",
                            "competitive_strategy", "icp_hypothesis",
                            "revenue_assumptions", "headcount_plan",
                            "break_even_analysis", "strategic_implications",
                        )
                    }
                    if key_fields:
                        summaries.append(
                            f"Section {sec}: {json.dumps(key_fields, default=str)[:500]}"
                        )
            if summaries:
                ctx["cross_section"] = (
                    "\n\nOTHER SECTIONS ALREADY COMPLETED:\n"
                    + "\n".join(summaries[:5])
                )

        hard_constraints = input_data.get("hard_constraints", {})
        if hard_constraints:
            lines = []
            for key, info in hard_constraints.items():
                if isinstance(info, dict):
                    lines.append(
                        f"  - {key} = {info.get('value')} "
                        f"(from {info.get('source', 'upstream')})"
                    )
            if lines:
                ctx["constraints"] = (
                    "\n\nBINDING CONSTRAINTS (use these exact numbers):\n"
                    + "\n".join(lines)
                )

        ceiling = input_data.get("confidence_ceiling")
        if ceiling:
            ctx["ceiling"] = (
                f"\n\nCONFIDENCE CEILING: Cannot exceed '{ceiling}' "
                "— upstream inputs have that confidence."
            )

        uncertainties = input_data.get("upstream_uncertainties", [])
        if uncertainties:
            lines = [
                f"  - [Section {u.get('from_section', '?')}] "
                f"{u.get('uncertainty', '')}"
                for u in uncertainties[:8]
            ]
            ctx["uncertainties"] = (
                "\n\nUPSTREAM UNCERTAINTIES (do not build confidently on these):\n"
                + "\n".join(lines)
            )

        ceo_provided = input_data.get("ceo_provided_data", {})
        if ceo_provided:
            ctx["ceo_data"] = (
                "\n\nCEO-PROVIDED DATA (prioritize over inferences):\n"
                + json.dumps(ceo_provided, indent=2, default=str)[:3000]
            )

        live_data = input_data.get("live_market_data", "")
        if live_data:
            ctx["live_data"] = (
                "\n\nLIVE MARKET DATA (retrieved from web, treat as current "
                "external evidence — verify source before treating as fact):\n"
                + str(live_data)[:2000]
            )

        if learning_context:
            ctx["learning"] = f"\n\n{learning_context}"

        return ctx

    def _format_context(self, ctx: dict) -> str:
        """Combine all context parts into a single string."""
        return "".join(ctx.values())

    def _accumulate_usage(self, total: dict, usage: dict) -> None:
        """Add usage from one call to running total."""
        total["input_tokens"] += usage.get("input_tokens", 0)
        total["output_tokens"] += usage.get("output_tokens", 0)

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
        """Make a single LLM call. Retries throttling up to `retries` times; timeouts get ONE retry only."""
        import asyncio
        from botocore.exceptions import (
            ReadTimeoutError,
            ConnectTimeoutError,
            ConnectionClosedError,
        )

        timeout_attempts = 0
        max_timeout_retries = 1

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
            except (
                self.bedrock.exceptions.ModelTimeoutException,
                ReadTimeoutError,
                ConnectTimeoutError,
                ConnectionClosedError,
            ):
                timeout_attempts += 1
                if timeout_attempts > max_timeout_retries:
                    logger.error("[IntelligenceEngine] Timeout/connection-closed — already retried %d time(s), giving up", max_timeout_retries)
                    return None, {}
                wait = 5
                logger.warning("[IntelligenceEngine] Timeout/connection-closed — retrying in %ds (attempt %d)", wait, timeout_attempts)
                await asyncio.sleep(wait)
            except Exception as e:
                logger.error("[IntelligenceEngine] LLM call failed: %s", e)
                return None, {}

        logger.error("[IntelligenceEngine] All %d retries exhausted", retries)
        return None, {}

    def _parse_output(self, raw: Optional[str]) -> Optional[dict]:
        """Parse JSON from raw LLM output with progressive repair for truncated responses."""
        if not raw:
            return None

        text = strip_markdown_json(raw)

        # Slice to outermost braces (handles prose before/after JSON)
        first_brace = text.find("{")
        if first_brace == -1:
            return None
        last_brace = text.rfind("}")
        if last_brace == -1:
            # Truncated output — no closing brace at all; take everything from first {
            text = text[first_brace:]
        else:
            text = text[first_brace:last_brace + 1]

        # Attempt 1: strict parse
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        # Attempt 2: json-repair (handles truncation, trailing commas, unescaped chars)
        try:
            from json_repair import repair_json
            repaired = repair_json(text)
            result = json.loads(repaired)
            if isinstance(result, dict):
                return result
        except Exception:
            pass

        return None

    @staticmethod
    def _normalize_confidence(parsed: dict) -> dict:
        """Coerce confidence_score to string enum (high/medium/low)."""
        conf = parsed.get("confidence_score")
        if conf is None:
            parsed["confidence_score"] = "low"
            return parsed
        if isinstance(conf, str):
            conf_lower = conf.strip().lower()
            if conf_lower in ("high", "medium", "low"):
                parsed["confidence_score"] = conf_lower
                return parsed
            try:
                conf = float(conf_lower)
            except ValueError:
                parsed["confidence_score"] = "low"
                return parsed
        if isinstance(conf, (int, float)):
            if conf > 0.66:
                parsed["confidence_score"] = "high"
            elif conf > 0.33:
                parsed["confidence_score"] = "medium"
            else:
                parsed["confidence_score"] = "low"
        return parsed
