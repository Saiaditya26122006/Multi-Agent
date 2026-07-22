"""Build v2 Phase 3 — grounding / provenance enforcement.

"No claim without a source." Given a section draft, extract its factual claims
and check each against the knowledge base (Alex's data + external_research +
conversation/decision). A claim is grounded if a KB chunk supports it above the
Titan-real similarity floor. The grounding report (rate + ungrounded claims +
supporting sources) travels with the section so Alex sees how evidence-backed a
draft is before accepting it.

Uses the existing RAG retrieval — evidence is the same store Feed writes to, so
web/DB data (Phase 4, stored via Feed with provenance) is checked the same way.
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Titan similarities are compressed; ~0.4 is "same idea" (see CLAUDE.md notes).
GROUNDING_THRESHOLD = 0.4
_EVIDENCE_SOURCES = ["ceo_doc", "external_research", "conversation", "decision"]


def check_claim(claim: str, session_id: Optional[str] = None) -> dict:
    """Is this claim supported by the KB? Returns grounded + best source."""
    try:
        from services.rag_service import retrieve

        chunks = retrieve(query=claim, source_types=_EVIDENCE_SOURCES, top_k=3, threshold=0.3)
        if not chunks:
            return {"claim": claim, "grounded": False, "best_similarity": 0.0, "source": None}
        best = chunks[0]
        return {
            "claim": claim,
            "grounded": best.similarity >= GROUNDING_THRESHOLD,
            "best_similarity": round(best.similarity, 3),
            "source": best.id,
            "source_type": best.source_type,
            "snippet": (best.content or "")[:140],
        }
    except Exception as e:  # noqa: BLE001
        logger.debug("[Grounding] check_claim failed: %s", e)
        return {"claim": claim, "grounded": False, "best_similarity": 0.0, "source": None, "error": True}


def extract_claims(text: str, max_claims: int = 12) -> list[str]:
    """Pull the checkable factual claims out of a section draft (one Haiku call).

    Returns [] on failure — callers treat grounding as unavailable, not zero.
    """
    if not text or not text.strip():
        return []
    try:
        from web.handlers.llm_helper import _get_client
        import os

        client = _get_client()
        model_id = os.getenv("CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
        system = (
            "Extract the distinct FACTUAL CLAIMS from this business-plan section — "
            "statements that assert something checkable (numbers, market facts, "
            "capabilities, decisions). Ignore generic filler, recommendations, and "
            "hedged opinions. Return ONLY a JSON array of short claim strings "
            f"(max {max_claims})."
        )
        resp = client.converse(
            modelId=model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": text[:6000]}]}],
            inferenceConfig={"maxTokens": 900},
        )
        raw = resp["output"]["message"]["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        items = json.loads(raw)
        return [c.strip() for c in items if isinstance(c, str) and len(c.strip()) > 3][:max_claims]
    except Exception as e:  # noqa: BLE001
        logger.debug("[Grounding] extract_claims failed: %s", e)
        return []


def _draft_to_text(draft) -> str:
    if draft is None:
        return ""
    if isinstance(draft, str):
        return draft
    try:
        return json.dumps(draft, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(draft)


def check_draft(draft, session_id: Optional[str] = None) -> dict:
    """Grounding report for a whole section draft.

    Returns {available, total_claims, grounded, rate, ungrounded[], details[]}.
    `available` is False when claim extraction couldn't run (don't treat as 0%).
    """
    claims = extract_claims(_draft_to_text(draft))
    if not claims:
        return {"available": False, "total_claims": 0, "grounded": 0, "rate": None,
                "ungrounded": [], "details": []}
    details = [check_claim(c, session_id) for c in claims]
    grounded = sum(1 for d in details if d.get("grounded"))
    ungrounded = [d["claim"] for d in details if not d.get("grounded")]
    return {
        "available": True,
        "total_claims": len(claims),
        "grounded": grounded,
        "rate": round(grounded / len(claims), 3),
        "ungrounded": ungrounded,
        "details": details,
    }
