"""Correction store — turns Alex's Feed review decisions into training signal.

Feed logs every confirm/adjust (feed_handler._record_correction -> record_correction
here) into the Supabase `feed_corrections` table. This module reads that table and
exposes it two ways:

  1. few-shot retrieval — given a new fact, find the most similar past
     Alex-placed facts, to show the classifier as examples ("Alex filed a
     similar fact under BP.9.2.1"). This is the mechanism that lets the
     classifier LEARN from real placements instead of static prompts.

  2. gold candidates — corrected entries (Alex changed the node) are the
     highest-signal labels; exported for review into the gold set.

Durability note: this used to append to evaluation/feed_corrections.jsonl on the
container filesystem, which is EPHEMERAL on Railway — wiped on every redeploy, so
no correction ever survived and the classifier never accumulated signal. It now
persists to Supabase. When the table is empty/absent, retrieval returns [] and
callers behave exactly as before (no few-shot), so this is safe before data
accrues and degrades gracefully if the migration hasn't been applied yet.

Correctness note: labels come only from real Alex actions — nothing here is invented.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

TABLE = "feed_corrections"

_sb_client = None


def _sb():
    """Service-role Supabase client (feed_corrections has no anon write policy)."""
    global _sb_client
    if _sb_client is None:
        from supabase import create_client

        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL and a Supabase key must be set in .env")
        _sb_client = create_client(url, key)
    return _sb_client


def record_correction(
    original_node_id: Optional[str],
    corrected_node_id: Optional[str],
    fact_content: Optional[str],
    correction_type: str,
    session_id: Optional[str] = None,
) -> None:
    """Persist one labeled Feed review decision to Supabase. Never raises.

    Args:
        original_node_id: What the classifier suggested.
        corrected_node_id: What Alex accepted (== original when confirmed).
        fact_content: The fact text.
        correction_type: "confirmed" | "corrected".
        session_id: Session that produced this decision.
    """
    try:
        _sb().table(TABLE).insert({
            "original_node_id": original_node_id,
            "corrected_node_id": corrected_node_id,
            "fact_content": (fact_content or "")[:500],
            "correction_type": correction_type,
            "session_id": session_id,
        }).execute()
    except Exception as e:  # noqa: BLE001
        logger.warning("[CorrectionStore] could not persist correction: %s", e)


def load_corrections(limit: int = 500) -> list[dict]:
    """Read logged corrections from Supabase (newest first). [] if none/unreadable.

    Returns dicts normalized to the keys the rest of this module expects:
    {fact, alex_chosen, system_suggested, action}.
    """
    try:
        rows = (
            _sb().table(TABLE)
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[CorrectionStore] could not read corrections: %s", e)
        return []

    out = []
    for r in rows:
        fact = r.get("fact_content")
        chosen = r.get("corrected_node_id")
        if fact and chosen:
            out.append({
                "fact": fact,
                "alex_chosen": chosen,
                "system_suggested": r.get("original_node_id"),
                "action": r.get("correction_type", ""),
            })
    return out


def similar_corrections(fact_text: str, k: int = 3, min_similarity: float = 0.4) -> list[dict]:
    """Return up to k past corrections most similar to `fact_text`.

    Embedding similarity (Titan) over the Supabase-stored corrections, best first.
    This is called BEFORE classification with the raw fact text (no candidate
    node exists yet), so similarity on fact content — not original_node_id match —
    is the correct few-shot signal. Empty if the table is empty or embedding is
    unavailable; callers must treat few-shot as optional.
    """
    corrections = load_corrections()
    if not corrections:
        return []
    try:
        import numpy as np
        from services.rag_service import embed

        q = np.array(embed(fact_text, input_type="search_query"))
        qn = np.linalg.norm(q) or 1.0
        scored = []
        for c in corrections:
            v = np.array(embed(c["fact"], input_type="search_document"))
            sim = float(np.dot(q, v) / (qn * (np.linalg.norm(v) or 1.0)))
            if sim >= min_similarity:
                scored.append({
                    "fact": c["fact"],
                    "node": c["alex_chosen"],
                    "action": c.get("action", ""),
                    "similarity": round(sim, 3),
                })
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:k]
    except Exception as e:  # noqa: BLE001
        logger.debug("[CorrectionStore] similar_corrections failed: %s", e)
        return []


def format_fewshot(examples: list[dict]) -> Optional[str]:
    """Format retrieved corrections as a compact few-shot block for a prompt."""
    if not examples:
        return None
    lines = ["Similar facts Alex has previously placed (use as guidance):"]
    for e in examples:
        lines.append(f'- "{e["fact"][:120]}" -> {e["node"]}')
    return "\n".join(lines)


def gold_candidates() -> list[dict]:
    """Corrected entries (Alex overrode the system) — highest-signal gold labels.

    Returns [{fact, proposed_node_id, source}] for review into gold_standard.json.
    Confirmed entries are excluded (they only tell us the system was already
    right); corrections are where the label is most informative.
    """
    out = []
    for c in load_corrections():
        if c.get("action") == "corrected":
            out.append({
                "fact": c["fact"],
                "proposed_node_id": c["alex_chosen"],
                "source": "alex_correction",
            })
    return out


def stats() -> dict:
    """Summary of what the store currently holds."""
    corrections = load_corrections()
    corrected = sum(1 for c in corrections if c.get("action") == "corrected")
    return {
        "total": len(corrections),
        "corrected": corrected,
        "confirmed": len(corrections) - corrected,
        "gold_candidates": corrected,
    }
