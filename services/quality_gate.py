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

def _review_personas() -> list[str]:
    """The full Council for gated sections — all configured personas, adding the
    adversarial Saboteur only when explicitly enabled (council_config)."""
    try:
        from config.phase2 import council_config as cc

        base = ["skeptic", "architect", "visionary", "stranger", "operator"]
        personas = [p for p in base if p in cc.COUNCIL_PERSONAS]
        if getattr(cc, "ENABLE_ADVERSARIAL_PERSONA", False) and "saboteur" in cc.COUNCIL_PERSONAS:
            personas.append("saboteur")
        return personas or ["skeptic"]
    except Exception:  # noqa: BLE001
        return ["skeptic"]


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


def coherence_check(section_id: str, draft, session_id: Optional[str]) -> Optional[dict]:
    """Cross-section coherence audit (the Devil's-Advocate / coherence role):
    does this section contradict the other completed sections?"""
    if not session_id:
        return None
    try:
        from services import section_state
        from web.handlers.llm_helper import _get_client
        import os

        states = section_state.list_sections(session_id)
        others = []
        for sid, s in states.items():
            if sid == section_id:
                continue
            d = s.get("draft")
            out = d.get("output") if isinstance(d, dict) else d
            if out and s.get("status") in ("done", "needs_review"):
                others.append(f"[Section {sid}] {json.dumps(out, ensure_ascii=False)[:800]}")
        if not others:
            return None

        client = _get_client()
        model_id = os.getenv("CLAUDE_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
        resp = client.converse(
            modelId=model_id,
            system=[{"text": (
                "You are a coherence auditor for a business plan. Identify CONTRADICTIONS "
                "between this section and the others (conflicting numbers, incompatible "
                "assumptions, misaligned strategy). Return ONLY JSON: "
                '{"contradictions": ["..."], "verdict": "pass|revise"}. Empty + pass if coherent.'
            )}],
            messages=[{"role": "user", "content": [{"text":
                f"THIS SECTION {section_id}:\n{_draft_text(draft)[:2500]}\n\n"
                f"OTHER SECTIONS:\n" + "\n".join(others)[:5000]}]}],
            inferenceConfig={"maxTokens": 500},
        )
        raw = resp["output"]["message"]["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        data = json.loads(raw)
        return {
            "contradictions": [c for c in (data.get("contradictions") or []) if isinstance(c, str)][:6],
            "verdict": data.get("verdict", "pass"),
        }
    except Exception as e:  # noqa: BLE001
        logger.debug("[QualityGate] coherence check failed: %s", e)
        return None


def critique_section(section_id: str, draft, session_id: Optional[str] = None) -> dict:
    """Full-Council critique of a gated section draft + cross-section coherence.

    Returns {gated, verdict ('pass'|'revise'), reviews:[...], coherence, issue_count}.
    Non-gated sections return gated=False and pass. Best-effort — never raises.
    """
    if not is_gated(section_id):
        return {"gated": False, "verdict": "pass", "reviews": [], "coherence": None, "issue_count": 0}
    text = _draft_text(draft)
    if len(text) < 20:
        return {"gated": True, "verdict": "pass", "reviews": [], "coherence": None, "issue_count": 0}

    reviews = [r for r in (_run_persona(p, section_id, text) for p in _review_personas()) if r]
    coherence = coherence_check(section_id, draft, session_id)
    issue_count = sum(len(r["issues"]) for r in reviews) + len((coherence or {}).get("contradictions", []))
    verdict = "pass"
    if any(r["verdict"] == "revise" for r in reviews) or (coherence and coherence.get("verdict") == "revise"):
        verdict = "revise"
    logger.info("[QualityGate] section %s: %s (%d issues, %d personas, coherence=%s)",
                section_id, verdict, issue_count, len(reviews),
                (coherence or {}).get("verdict"))
    return {"gated": True, "verdict": verdict, "reviews": reviews,
            "coherence": coherence, "issue_count": issue_count}
