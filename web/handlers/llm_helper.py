"""
Lightweight LLM helper for workspace handlers.

Calls Claude Haiku via Bedrock to synthesize answers from RAG chunks.
"""

import os
import logging
import json
import re

import boto3
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_bedrock_client = None


def _get_client():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
        )
    return _bedrock_client


def generate_answer(question: str, chunks: list, system_prompt: str = "") -> str:
    """Call Claude Haiku to synthesize an answer from RAG chunks.

    Args:
        question: The user's question.
        chunks: List of RAG chunk objects with .content, .epistemic_status, .source_type.
        system_prompt: Optional system prompt override.

    Returns:
        Claude's synthesized answer string.
    """
    if not chunks:
        return "No relevant data found in the knowledge base for that question."

    context_parts = []
    for i, c in enumerate(chunks, 1):
        status = f"[{c.epistemic_status}]" if c.epistemic_status else "[UNKNOWN]"
        source = c.source_type or "unknown"
        context_parts.append(f"{i}. {status} (source: {source}) {c.content[:300]}")

    context_block = "\n".join(context_parts)

    if not system_prompt:
        system_prompt = (
            "You are the EpistemicOS assistant helping Alex (CEO) understand his business plan. "
            "Answer based ONLY on the knowledge base context provided. "
            "Be concise, direct, and specific. Use 2-5 sentences max. "
            "Never invent data not in the context. "
            "When referencing epistemic status or data quality, translate internal tags into plain language. "
            "For example: [ASSUMPTION] becomes 'this is an assumption, not confirmed'; "
            "[CONFIRMED] becomes 'this is confirmed'; "
            "[CONTRADICTION] becomes 'this conflicts with other information'; "
            "[MISSING] becomes 'there is no evidence for this yet'; "
            "[LOW CONFIDENCE] becomes 'confidence is low on this'. "
            "Never output raw tags like [ASSUMPTION] or [MISSING] in your response. "
            "Always write in clear, natural sentences."
        )

    user_message = (
        f"KNOWLEDGE BASE CONTEXT:\n{context_block}\n\n"
        f"CEO'S QUESTION: {question}\n\n"
        "Answer naturally and concisely based on the context above."
    )

    try:
        client = _get_client()
        model_id = os.getenv("CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

        response = client.converse(
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 512},
        )

        answer = response["output"]["message"]["content"][0]["text"]
        logger.info(
            "[LLMHelper] Generated answer (%d tokens in, %d tokens out)",
            response.get("usage", {}).get("inputTokens", 0),
            response.get("usage", {}).get("outputTokens", 0),
        )
        return answer

    except Exception as e:
        logger.error("[LLMHelper] Bedrock call failed: %s", e)
        return _fallback_response(chunks)


def _fallback_response(chunks: list) -> str:
    """Return raw chunks if LLM call fails."""
    lines = ["Here's what the knowledge base contains (LLM unavailable):"]
    for i, c in enumerate(chunks[:5], 1):
        status = f"[{c.epistemic_status}]" if c.epistemic_status else ""
        lines.append(f"  {i}. {status} {c.content[:150]}")
    return "\n".join(lines)


DOMAIN_CLASSIFY_SYSTEM_PROMPT = """You are a business-plan domain classifier. You are given ONE fact/claim and a list of top-level business plan domains (BP.1, BP.2, etc.). Your job is to pick 1-3 domains that this fact most likely belongs under.

Each domain has a title and purpose. Pick based on WHAT the fact is fundamentally about, not keyword matching.

## Critical Pattern Recognition

**Jobs-To-Be-Done (JTBD) in PMF context:**
- "Job: Improve X quality" → BP.2 (PMF/Demand) or BP.10 (PMF Evidence), NOT BP.1 (Product) or BP.6 (Business Model)
- "Outcome: X evaluated" in PMF doc → BP.2 (Demand) or BP.10 (Evidence)
- Why: JTBD statements in PMF analysis are evidence of DEMAND, not product features

**Urgency/Need statements:**
- "Institution needs/wants/requires X" → BP.2 (Urgency/Demand)
- "Institution concludes: We need X" → BP.2 (Urgency Hypothesis)
- NOT BP.1 (Workflow) — don't confuse workflow description with urgency signal

**PMF Definitions/Evidence:**
- "PMF is/is not..." → BP.10 (PMF Evidence)
- "PMF exists when..." → BP.10 (PMF Evidence Framework)
- Any fact about what counts/doesn't count as PMF → BP.10

**Workflow/Product:**
- "The product does X" → BP.1 (Product Definition)
- "Workflow: step 1, step 2" → BP.1 (Workflow Served)
- Only use BP.1 for internal product/workflow DEFINITIONS, not demand signals

Response format (JSON only, no markdown):
{"domains": ["BP.X", "BP.Y"], "reasoning": "one sentence why"}

Rules:
- Return 1-3 domain IDs from the list provided, in order of relevance
- If uncertain between domains, include both (max 3 total)
- Be decisive — empty list is not allowed
- When document context mentions "PMF analysis", strongly prefer BP.2 and BP.10 over BP.1 and BP.6"""

CLASSIFY_SYSTEM_PROMPT = """You are a classification engine for a business-plan knowledge architecture. You are given ONE fact/claim and a shortlist of candidate architecture nodes — each with an id, title, purpose, required output, and (where relevant) claims/inferences that node is explicitly PROHIBITED from making.

Your job is to pick the SINGLE most accurate node this fact belongs under.

## CRITICAL: Specificity vs Generality

**For GENERAL statements** (hypotheses, definitions, frameworks, high-level descriptions):
- Prefer PARENT nodes (BP.2.3, BP.10.3.1) over CHILD nodes (BP.2.3.4, BP.10.3.1.2)
- Example: "Institution needs quality tools" → BP.2.3 (Urgency Hypothesis), NOT BP.2.3.4 (specific trigger)
- Example: "PMF means market pull" → BP.10.3.1 (Framework), NOT BP.10.3.1.3 (specific metric)

**For SPECIFIC data** (individual metrics, specific workflow steps, concrete constraints):
- Prefer CHILD nodes that match the specific data type
- Example: "Q4 revenue: $1.2M" → BP.9.5.1 (specific revenue data), NOT BP.9 (general financials)
- Example: "Step 3: Manager approval" → BP.1.3.2.3 (specific step), NOT BP.1.3 (general workflow)

## Rule of thumb:
If the fact is a high-level statement or hypothesis, prefer the parent node.
If the fact is specific, measurable data, prefer the detailed child node.

## CRITICAL: Prohibition Check (do this FIRST for every candidate)

Read the `prohibited:` line for EACH candidate. If filing this fact under a node would assert, support, or imply something that node explicitly prohibits, that node is DISQUALIFIED — even if the title or keywords look like a perfect match. A node that prohibits "inferring X" cannot store a fact that claims or implies X.

## CRITICAL: Required Output Check (do this SECOND)

For the candidate you're considering, read its `required_output:` field. Ask yourself: "Would this fact actually contribute to PRODUCING that required output?" If the answer is no — if the fact merely shares keywords with the node title but doesn't inform or produce the stated output — that node is NOT a fit.

## CRITICAL: None Fit

If NO candidate's required_output genuinely matches what this fact provides, return none_fit=true. Do NOT force a weak match. It is better to return none_fit=true than to file a fact under a wrong node.

## WRONG CLASSIFICATION EXAMPLES (learn from these):

1. WRONG: "one email from a dean doesn't count as PMF" -> BP.10.3.2 (PMF Stage Definitions)
   WHY WRONG: BP.10.3.2 defines stage thresholds. This fact is about what does NOT qualify as PMF evidence — it belongs in BP.10.3.8 (Prohibited PMF Inferences) or similar prohibition node.

2. WRONG: "improve manuscript quality" -> BP.1.2.1 (Writing Assistance Exclusion)
   WHY WRONG: BP.1.2.1 PROHIBITS claims about manuscript improvement. Filing a fact about improving manuscripts under a node that bars exactly that claim is a direct prohibition violation.

3. WRONG: "product diagnostic workflow" -> BP.5.4.3 (Approval Workflow)
   WHY WRONG: BP.5.4.3's required_output is about procurement sign-off processes, not product diagnostic workflows. Keyword "workflow" matched, but the required_output is completely unrelated.

## Response Format

Respond with ONLY a JSON object, no markdown fences, no commentary, in exactly this shape:
{"node_id": "<id from the candidate list, or null if none genuinely fit>", "confidence": "high" | "medium" | "low", "reasoning": "<one short sentence>", "none_fit": true | false}

## Rules:
- Only choose a node_id that appears in the candidate list you were given. Never invent one.
- If none of the candidates are a genuine fit — including because every plausible match is ruled out by its prohibited-claims line — set "none_fit": true and "node_id": null. Do not force a weak or prohibited match.
- "confidence" reflects how well the fact PRODUCES or INFORMS the required_output of the node you picked, not just keyword similarity.
- A title/keyword match alone is NEVER sufficient for "high" confidence — the fact must genuinely contribute to the node's required_output AND not violate any prohibition.
- Be decisive. Pick exactly one node, not a list."""


def classify_fact_to_domain(
    fact_text: str,
    domains: list[dict],
    document_context: str = None,
) -> list[str]:
    """Stage 1: Pick 1-3 top-level domains (BP.1, BP.2, etc.) for a fact.

    This narrows the search space from ~745 nodes to 1-3 domains before
    Stage 2 picks the specific node. Mirrors how Alex's Claude Desktop
    achieved 9/9 accuracy by reasoning at the domain level first.

    Args:
        fact_text: The atomic fact/claim to classify.
        domains: List of level-1 domain dicts with node_id, node_title, purpose.
        document_context: Optional document summary for disambiguation.

    Returns:
        List of 1-3 domain IDs (e.g., ["BP.2", "BP.10"]) in relevance order.
        Falls back to ["BP.1"] if LLM fails or returns empty/invalid.
    """
    if not domains:
        return ["BP.1"]  # Fallback

    domain_block = "\n".join(
        f"- {d['node_id']}: {d.get('node_title','')} — {(d.get('purpose') or '')[:200]}"
        for d in domains
    )

    context_block = ""
    if document_context:
        context_block = f"DOCUMENT CONTEXT:\n{document_context}\n\n"

    user_message = (
        f"{context_block}"
        f"FACT TO CLASSIFY:\n\"{fact_text}\"\n\n"
        f"DOMAINS:\n{domain_block}\n\n"
        "Return the JSON object now."
    )

    try:
        client = _get_client()
        # Sonnet for domain classification — this is the most critical step.
        # Wrong domain = correct node never in candidate pool = guaranteed miss.
        model_id = os.getenv("CLAUDE_SONNET_MODEL", "us.anthropic.claude-sonnet-4-6")

        response = client.converse(
            modelId=model_id,
            system=[{"text": DOMAIN_CLASSIFY_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 150},
        )

        raw = response["output"]["message"]["content"][0]["text"].strip()
        # Strip markdown code blocks if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)

        parsed = json.loads(raw)
        domain_ids = parsed.get("domains", [])

        # Validate: must be list of 1-3 BP.X strings
        valid_domain_pattern = re.compile(r"^BP\.\d+$")
        domain_ids = [d for d in domain_ids if valid_domain_pattern.match(str(d))][:3]

        if not domain_ids:
            logger.warning("[LLMHelper] Domain classification returned empty, using BP.1 fallback")
            return ["BP.1"]

        logger.info("[LLMHelper] Fact classified to domains: %s", domain_ids)
        return domain_ids

    except Exception as e:
        logger.error("[LLMHelper] Domain classification failed: %s", e)
        return ["BP.1"]


def classify_fact_to_node(
    fact_text: str,
    candidates: list[dict],
    document_context: str = None,
    use_fast_model: bool = False,
) -> dict:
    """Ask Claude to pick the single best architecture node for one fact.

    This replaces raw embedding-similarity ranking as the final call: the
    embedding step (services.rag_service / feed_handler._direct_node_match)
    is still used first to narrow ~745 nodes down to a shortlist (cheap,
    local, no API call), but an LLM reasoning over that shortlist's actual
    purpose/required_output text is far more discriminating than cosine
    similarity on a small sentence-transformer embedding — which is exactly
    the accuracy gap Alex saw versus a manual ChatGPT pass on the same data.

    Args:
        fact_text: The atomic fact/claim to classify.
        candidates: Pre-filtered node dicts (from match_bp_node /
            _direct_node_match), each with node_id, node_title, purpose.
            Keep this list small (~10-20) — it's the entire prompt context,
            not the full architecture.
        document_context: Optional 1-2 sentence summary of the larger
            document this fact was extracted from. Helps disambiguate facts
            that are ambiguous in isolation.
        use_fast_model: If True, use Haiku instead of Sonnet for speed.
            Used for batch classification where the prohibition gate and
            human review queue catch any errors. Sonnet is still used for
            single-fact classification where latency is less critical.

    Returns:
        Dict with: node_id (str or None), node_title (str), confidence
        ("high"/"medium"/"low"), reasoning (str), none_fit (bool). Falls
        back to the top embedding candidate with confidence="low" if the
        LLM call fails or returns something unparseable — never crashes
        the caller, and never silently invents a node_id not in the list.
    """
    by_id = {c["node_id"]: c for c in candidates}

    if not candidates:
        return {
            "node_id": None, "node_title": "", "confidence": "low",
            "reasoning": "No candidates to classify against.", "none_fit": True,
        }

    # Defensive: a handful of real architecture nodes have node_title/purpose
    # set to null (not just absent), so `.get(key, "")` alone doesn't catch
    # it — the default only kicks in when the key is missing. `or ""` guards
    # against a bare .lower()/slice crashing on None from one of those nodes.
    #
    # required_output and prohibited_claims are only present since the
    # 2026-07 architecture re-export added the richer per-node schema —
    # older cached candidates (or a future export that drops them again)
    # simply won't have the keys, so .get(...) with no second arg is
    # intentional here: missing is fine, it just renders as an empty line.
    def _candidate_line(c: dict) -> str:
        title = c.get("node_title") or ""
        purpose = (c.get("purpose") or "")[:250]
        required_output = (c.get("required_output") or "")[:150]
        prohibited = (c.get("prohibited_claims") or "")[:150]
        # Fix #5: Make purpose more prominent by putting it on its own line
        line = f"- {c['node_id']}: {title}\n  PURPOSE: {purpose}"
        if required_output:
            line += f"\n  Output: {required_output}"
        if prohibited:
            line += f"\n  Prohibited: {prohibited}"
        return line

    candidate_block = "\n".join(_candidate_line(c) for c in candidates)

    context_block = ""
    if document_context:
        context_block = f"DOCUMENT CONTEXT (this fact was extracted from a larger text about):\n{document_context}\n\n"

    # Build few-shot examples as message history
    # CRITICAL: These examples must NOT overlap with test/validation sets
    # Focus on teaching patterns, not memorizing specific cases
    few_shot_examples = [
        # Pattern 1: PMF prohibition vs definition
        {
            "user": (
                "DOCUMENT CONTEXT: PMF framework documentation\n\n"
                "FACT TO CLASSIFY:\n\"Satisfaction surveys are not valid PMF evidence\"\n\n"
                "CANDIDATE NODES:\n"
                "- BP.10.3.1: PMF Evidence Framework\n  PURPOSE: Define overall framework for PMF assessment\n"
                "- BP.10.3.8: Prohibited PMF Inferences\n  PURPOSE: Define conclusions that PMF evidence must never support\n"
                "Return the JSON object now."
            ),
            "assistant": '{"node_id": "BP.10.3.8", "confidence": "high", "reasoning": "Statement about what is NOT valid PMF evidence belongs in prohibited inferences", "none_fit": false}',
        },
        # Pattern 2: JTBD statement vs JTBD framework
        {
            "user": (
                "DOCUMENT CONTEXT: User research findings\n\n"
                "FACT TO CLASSIFY:\n\"Researcher needs to verify data accuracy efficiently\"\n\n"
                "CANDIDATE NODES:\n"
                "- BP.2.1.1: Problem Statement\n  PURPOSE: State the governed problem claim\n"
                "- BP.6.1.3: JTBD Framework\n  PURPOSE: Define the jobs-to-be-done framework governing how user jobs are identified\n"
                "Return the JSON object now."
            ),
            "assistant": '{"node_id": "BP.2.1.1", "confidence": "high", "reasoning": "Actual user need statement is a problem statement - BP.6.1.3 defines JTBD methodology not data", "none_fit": false}',
        },
        # Pattern 3: Urgency signal vs workflow description
        {
            "user": (
                "DOCUMENT CONTEXT: Customer discovery interview\n\n"
                "FACT TO CLASSIFY:\n\"Professor stated this is a critical priority for the department\"\n\n"
                "CANDIDATE NODES:\n"
                "- BP.1.3: Workflow Served\n  PURPOSE: Defines the workflow the product supports\n"
                "- BP.2.3: Urgency and Priority Hypothesis\n  PURPOSE: Defines whether the problem is urgent or time-sensitive\n"
                "Return the JSON object now."
            ),
            "assistant": '{"node_id": "BP.2.3", "confidence": "high", "reasoning": "Critical/priority/urgent statements indicate urgency hypothesis not workflow", "none_fit": false}',
        },
        # Pattern 4: Business model revenue vs pricing strategy
        {
            "user": (
                "DOCUMENT CONTEXT: Business plan financials\n\n"
                "FACT TO CLASSIFY:\n\"Revenue streams: subscription fees and implementation services\"\n\n"
                "CANDIDATE NODES:\n"
                "- BP.5.1: Revenue Model\n  PURPOSE: Define how the business generates revenue\n"
                "- BP.9.2.1: Pricing Structure\n  PURPOSE: Define pricing tiers and amounts\n"
                "Return the JSON object now."
            ),
            "assistant": '{"node_id": "BP.5.1", "confidence": "high", "reasoning": "Revenue streams definition is business model not pricing structure", "none_fit": false}',
        },
        # Pattern 5: Risk vs assumption
        {
            "user": (
                "DOCUMENT CONTEXT: Risk assessment document\n\n"
                "FACT TO CLASSIFY:\n\"Risk: Market may not be willing to pay premium pricing\"\n\n"
                "CANDIDATE NODES:\n"
                "- BP.12.1: Identified Risks\n  PURPOSE: Document known risks to business success\n"
                "- BP.12.2: Key Assumptions\n  PURPOSE: Document unvalidated assumptions\n"
                "Return the JSON object now."
            ),
            "assistant": '{"node_id": "BP.12.1", "confidence": "high", "reasoning": "Explicitly labeled as risk with potential negative outcome", "none_fit": false}',
        },
        # Pattern 6: Workflow description vs workflow step detail
        {
            "user": (
                "DOCUMENT CONTEXT: Product specification\n\n"
                "FACT TO CLASSIFY:\n\"System supports multi-stage review and approval process\"\n\n"
                "CANDIDATE NODES:\n"
                "- BP.1.3: Workflow Served\n  PURPOSE: Defines the workflow the product supports\n"
                "- BP.1.3.2: Workflow Steps\n  PURPOSE: Define individual steps in the workflow\n"
                "Return the JSON object now."
            ),
            "assistant": '{"node_id": "BP.1.3", "confidence": "high", "reasoning": "High-level workflow description goes in parent node not detailed steps", "none_fit": false}',
        },
    ]

    # Build message history with few-shot examples
    messages = []
    for example in few_shot_examples:
        messages.append({"role": "user", "content": [{"text": example["user"]}]})
        messages.append({"role": "assistant", "content": [{"text": example["assistant"]}]})

    # Add the actual classification request
    user_message = (
        f"{context_block}"
        f"FACT TO CLASSIFY:\n\"{fact_text}\"\n\n"
        f"CANDIDATE NODES:\n{candidate_block}\n\n"
        "Return the JSON object now."
    )
    messages.append({"role": "user", "content": [{"text": user_message}]})

    try:
        client = _get_client()
        if use_fast_model:
            model_id = os.getenv("CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
        else:
            model_id = os.getenv("CLAUDE_SONNET_MODEL", "us.anthropic.claude-sonnet-4-6")

        response = client.converse(
            modelId=model_id,
            system=[{"text": CLASSIFY_SYSTEM_PROMPT}],
            messages=messages,
            inferenceConfig={"maxTokens": 300},
        )

        raw = response["output"]["message"]["content"][0]["text"]
        parsed = _parse_classification_response(raw, by_id)
        logger.info(
            "[LLMHelper] Classified fact -> %s (confidence=%s)",
            parsed.get("node_id"), parsed.get("confidence"),
        )
        return parsed

    except Exception as e:
        logger.error("[LLMHelper] Classification call failed, falling back to top embedding candidate: %s", e)
        top = candidates[0]
        return {
            "node_id": top["node_id"], "node_title": top.get("node_title", ""),
            "confidence": "low",
            "reasoning": "LLM classification unavailable — used top embedding match instead.",
            "none_fit": False,
        }


def _parse_classification_response(raw: str, by_id: dict) -> dict:
    """Parse the LLM's JSON reply, stripping markdown fences if present.

    Never trusts a node_id the model invents that wasn't in the candidate
    list handed to it — falls back to the top candidate instead, same as
    an outright call failure, so a malformed or hallucinated reply can't
    silently create a bogus node reference downstream.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("[LLMHelper] Non-JSON classification response, using fallback: %r", raw[:200])
        top = next(iter(by_id.values()), None)
        if top is None:
            return {"node_id": None, "node_title": "", "confidence": "low", "reasoning": "Unparseable response.", "none_fit": True}
        return {
            "node_id": top["node_id"], "node_title": top.get("node_title", ""),
            "confidence": "low", "reasoning": "Unparseable classification response — used top embedding match.",
            "none_fit": False,
        }

    node_id = data.get("node_id")
    none_fit = bool(data.get("none_fit")) or node_id is None

    if node_id and node_id not in by_id:
        logger.warning("[LLMHelper] LLM returned node_id %r not in candidate list — ignoring, treating as no fit.", node_id)
        node_id = None
        none_fit = True

    node_title = by_id[node_id]["node_title"] if node_id and node_id in by_id else ""
    confidence = data.get("confidence") if data.get("confidence") in ("high", "medium", "low") else "medium"

    return {
        "node_id": node_id,
        "node_title": node_title,
        "confidence": confidence,
        "reasoning": str(data.get("reasoning", ""))[:200],
        "none_fit": none_fit,
    }


_VALIDATE_SYSTEM_PROMPT = (
    "You are a binary validator. You will be given a fact and a node's required_output "
    "and prohibited_claims. Answer TWO questions with YES or NO only, then one sentence of reasoning.\n\n"
    "Respond with ONLY a JSON object, no markdown fences:\n"
    '{"required_output_match": true/false, "prohibition_violated": true/false, "reasoning": "<one sentence>"}\n\n'
    "Question 1 (required_output_match): Does this fact produce, inform, or contribute to "
    "generating the stated required_output? If the fact merely shares keywords but would NOT "
    "help produce that output, answer false.\n\n"
    "Question 2 (prohibition_violated): Does filing this fact under this node assert, support, "
    "or imply any claim listed in the prohibited_claims? If the fact says or implies something "
    "the node explicitly bars, answer true."
)


def validate_classification(fact_text: str, node_details: dict) -> dict:
    """Post-classification LLM validation: checks required_output fit and prohibition.

    Makes ONE Haiku call to verify the LLM classifier's pick is semantically
    correct — catches cases where keyword similarity fooled the initial pass.

    SPECIAL HANDLING: For definitional nodes (Framework, Definition, Register,
    Prohibited, Hypothesis), uses a different validation question: "Does this
    fact DEFINE, EXEMPLIFY, or BELONG TO the concept?" instead of "Does it
    PRODUCE the output?". This is because definitional nodes store the
    definitions themselves, not data that produces definitions.

    Args:
        fact_text: The fact being classified.
        node_details: Dict with at least 'required_output' and
            'prohibited_claims_inference_patterns' keys.

    Returns:
        Dict with: required_output_match (bool), prohibition_violated (bool),
        reasoning (str). On failure, returns a permissive default (match=True,
        violated=False) so the caller doesn't reject valid classifications
        when the validation call itself is flaky.
    """
    required_output = (node_details.get("required_output") or "")[:300]
    prohibited_claims = (node_details.get("prohibited_claims_inference_patterns") or "")[:300]
    node_title = (node_details.get("node_title") or "").lower()
    node_purpose = (node_details.get("purpose") or "")[:200]

    if not required_output and not prohibited_claims:
        return {
            "required_output_match": True,
            "prohibition_violated": False,
            "reasoning": "No required_output or prohibited_claims to validate against.",
        }

    # Check if this is a definitional/governance node
    DEFINITIONAL_PATTERNS = [
        "definition", "framework", "register", "hypothesis", "prohibited",
        "inference", "evidence framework", "assumption", "boundary",
    ]
    is_definitional = any(pattern in node_title for pattern in DEFINITIONAL_PATTERNS)

    if is_definitional:
        # Use different validation question for definitional nodes
        user_message = (
            f"FACT: \"{fact_text}\"\n\n"
            f"NODE: {node_details.get('node_id')} - {node_details.get('node_title')}\n"
            f"NODE PURPOSE: {node_purpose}\n"
            f"NODE TYPE: Definitional/Governance (stores definitions, frameworks, or prohibitions)\n\n"
            f"NODE PROHIBITED CLAIMS: \"{prohibited_claims}\"\n\n"
            "SPECIAL INSTRUCTION: This is a DEFINITIONAL node. It stores definitions, "
            "frameworks, or prohibited patterns — NOT data that produces those definitions. "
            "The fact itself IS the definition/example/prohibition.\n\n"
            "Question 1 (required_output_match): Does this fact DEFINE, EXEMPLIFY, STATE, "
            "or BELONG TO the node's concept/purpose? Examples:\n"
            "- For 'PMF Evidence Framework' node: 'PMF exists when...' IS a definition → YES\n"
            "- For 'Prohibited PMF Inferences' node: 'PMF is not...' IS a prohibition → YES\n"
            "- For 'Hypothesis' node: 'We assume X' IS a hypothesis → YES\n\n"
            "Return the JSON now."
        )
    else:
        # Standard validation for data nodes
        user_message = (
            f"FACT: \"{fact_text}\"\n\n"
            f"NODE REQUIRED OUTPUT: \"{required_output}\"\n\n"
            f"NODE PROHIBITED CLAIMS: \"{prohibited_claims}\"\n\n"
            "Return the JSON now."
        )

    try:
        client = _get_client()
        model_id = os.getenv("CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

        response = client.converse(
            modelId=model_id,
            system=[{"text": _VALIDATE_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 150},
        )

        raw = response["output"]["message"]["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

        data = json.loads(raw)
        result = {
            "required_output_match": bool(data.get("required_output_match", True)),
            "prohibition_violated": bool(data.get("prohibition_violated", False)),
            "reasoning": str(data.get("reasoning", ""))[:200],
        }
        logger.info(
            "[LLMHelper] Validation result: match=%s, violated=%s — %s",
            result["required_output_match"], result["prohibition_violated"],
            result["reasoning"][:80],
        )
        return result

    except Exception as e:
        logger.error("[LLMHelper] Validation call failed (permissive fallback): %s", e)
        return {
            "required_output_match": True,
            "prohibition_violated": False,
            "reasoning": f"Validation call failed: {e}",
        }
