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


CLASSIFY_SYSTEM_PROMPT = """You are a classification engine for a business-plan knowledge architecture. You are given ONE fact/claim and a shortlist of candidate architecture nodes — each with an id, title, purpose, required output, and (where relevant) claims/inferences that node is explicitly PROHIBITED from making. Your only job is to pick the SINGLE most specific, most accurate node this fact belongs under — prefer the deepest/most specific node whose purpose genuinely matches the fact's content, not just a top-level domain.

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
        purpose = (c.get("purpose") or "")[:200]
        required_output = (c.get("required_output") or "")[:150]
        prohibited = (c.get("prohibited_claims") or "")[:150]
        line = f"- {c['node_id']}: {title} — {purpose}"
        if required_output:
            line += f" | required_output: {required_output}"
        if prohibited:
            line += f" | prohibited: {prohibited}"
        return line

    candidate_block = "\n".join(_candidate_line(c) for c in candidates)

    context_block = ""
    if document_context:
        context_block = f"DOCUMENT CONTEXT (this fact was extracted from a larger text about):\n{document_context}\n\n"

    user_message = (
        f"{context_block}"
        f"FACT TO CLASSIFY:\n\"{fact_text}\"\n\n"
        f"CANDIDATE NODES:\n{candidate_block}\n\n"
        "Return the JSON object now."
    )

    try:
        client = _get_client()
        if use_fast_model:
            model_id = os.getenv("CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
        else:
            model_id = os.getenv("CLAUDE_SONNET_MODEL", "us.anthropic.claude-sonnet-4-6")

        response = client.converse(
            modelId=model_id,
            system=[{"text": CLASSIFY_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
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

    if not required_output and not prohibited_claims:
        return {
            "required_output_match": True,
            "prohibition_violated": False,
            "reasoning": "No required_output or prohibited_claims to validate against.",
        }

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
