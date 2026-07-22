"""Build v2 Phase 5 — per-section quality gate (revives Council/Devil's Advocate).

The Council personas and Devil's Advocate exist in the codebase but the live
orchestrator never invoked them. This wires a real adversarial critique back
into the live per-section flow: gated sections (config.phase2.council_config
COUNCIL_GATED_SECTIONS) get reviewed by the Skeptic + Operator personas before
Alex accepts. The critique (issues + verdict) travels with the section draft.

Kept intentionally lightweight (2 personas, Haiku) so it runs per section without
the heavy multi-round council-agent machinery; the full 6-persona deliberation
remains available for deeper audits.
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Personas to run per gated section (skeptic = flaws/evidence; operator = feasibility).
_REVIEW_PERSONAS = ["skeptic", "operator"]


def is_gated(section_id: str) -> bool:
    try:
        from config.phase2 import council_config as cc
        return str(section_id) in cc.COUNCIL_GATED_SECTIONS
    except Exception:  # noqa: BLE001
        return False


def _draft_text(draft) -> str:
    if draft is None:
        return ""
    if isinstance(draft, dict):
        draft = draft.get("output", draft)
    if isinstance(draft, str):
        return draft
    try:
        return json.dumps(draft, ensure_ascii=False)[:6000]
    except Exception:  # noqa: BLE001
        return str(draft)[:6000]


def _run_persona(persona_key: str, section_id: str, draft_text: str) -> Optional[dict]:
    try:
        from config.phase2 import council_config as cc
        from web.handlers.llm_helper import _get_client
        import os

        persona = cc.COUNCIL_PERSONAS.get(persona_key)
        if not persona:
            return None
        client = _get_client()
        model_id = os.getenv("CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
        system = persona["system_prompt"] + (
            "\n\nReturn ONLY JSON: {\"issues\": [\"...\"], \"verdict\": \"pass|revise\"}. "
            "Be specific and cite exact claims. Empty issues + pass if it is sound."
        )
        resp = client.converse(
            modelId=model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text":
                f"SECTION {section_id} OUTPUT:\n{draft_text}\n\nReview it."}]}],
            inferenceConfig={"maxTokens": 500},
        )
        raw = resp["output"]["message"]["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)
        return {
            "persona": persona.get("name", persona_key),
            "icon": persona.get("icon", ""),
            "issues": [i for i in (data.get("issues") or []) if isinstance(i, str)][:8],
            "verdict": data.get("verdict", "pass"),
        }
    except Exception as e:  # noqa: BLE001
        logger.debug("[QualityGate] persona %s failed: %s", persona_key, e)
        return None


def critique_section(section_id: str, draft) -> dict:
    """Adversarial critique of a gated section draft.

    Returns {gated, verdict ('pass'|'revise'), reviews:[...], issue_count}.
    Non-gated sections return gated=False and pass. Best-effort — never raises.
    """
    if not is_gated(section_id):
        return {"gated": False, "verdict": "pass", "reviews": [], "issue_count": 0}
    text = _draft_text(draft)
    if len(text) < 20:
        return {"gated": True, "verdict": "pass", "reviews": [], "issue_count": 0}

    reviews = [r for r in (_run_persona(p, section_id, text) for p in _REVIEW_PERSONAS) if r]
    issue_count = sum(len(r["issues"]) for r in reviews)
    verdict = "revise" if any(r["verdict"] == "revise" for r in reviews) else "pass"
    logger.info("[QualityGate] section %s: %s (%d issues from %d personas)",
                section_id, verdict, issue_count, len(reviews))
    return {"gated": True, "verdict": verdict, "reviews": reviews, "issue_count": issue_count}
