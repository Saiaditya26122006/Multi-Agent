"""Correction store — turns Alex's Feed review decisions into training signal.

Feed logs every confirm/adjust to evaluation/feed_corrections.jsonl
(_record_correction). This module reads that log and exposes it two ways:

  1. few-shot retrieval — given a new fact, find the most similar past
     Alex-placed facts, to show the classifier as examples ("Alex filed a
     similar fact under BP.9.2.1"). This is the mechanism that lets the
     classifier LEARN from real placements instead of static prompts.

  2. gold candidates — corrected entries (Alex changed the node) are the
     highest-signal labels; exported for review into the gold set.

Correctness note: labels come only from real Alex actions — nothing here is
invented. When the log is empty, retrieval returns [] and callers behave exactly
as before (no few-shot), so wiring it in is safe before data accumulates.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CORRECTIONS = Path(__file__).parent.parent / "evaluation" / "feed_corrections.jsonl"


def load_corrections() -> list[dict]:
    """Read all logged corrections (newest last). Empty list if none/unreadable."""
    if not _CORRECTIONS.exists():
        return []
    out = []
    try:
        for line in _CORRECTIONS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("fact") and rec.get("alex_chosen"):
                    out.append(rec)
            except json.JSONDecodeError:
                continue
    except Exception as e:  # noqa: BLE001
        logger.warning("[CorrectionStore] could not read log: %s", e)
    return out


def similar_corrections(fact_text: str, k: int = 3, min_similarity: float = 0.4) -> list[dict]:
    """Return up to k past corrections most similar to `fact_text`.

    Uses the same Titan embeddings as the rest of the system. Returns
    [{fact, node, action, similarity}], best first. Empty if the log is empty or
    embedding is unavailable — callers must treat few-shot as optional.
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
    """Summary of what the log currently holds."""
    corrections = load_corrections()
    corrected = sum(1 for c in corrections if c.get("action") == "corrected")
    return {
        "total": len(corrections),
        "corrected": corrected,
        "confirmed": len(corrections) - corrected,
        "gold_candidates": corrected,
    }
