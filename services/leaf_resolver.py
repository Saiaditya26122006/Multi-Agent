"""Deferred leaf resolution.

Facts Feed was unsure about are filed provisionally at the SECTION (BP.x.y).
This resolves them to their exact leaf when there is a confident answer, by
re-classifying each among its section's children.

Measured (evaluation/context_experiment.py): section-constrained resolution is
+20 pts over the isolated ingestion call (32% -> 52% exact leaf). Sibling-fact
context does NOT help (-5 pts), so this deliberately does not inject neighbours.

SUGGESTION MODE (why we don't auto-promote): measured gated precision is only
59% at high confidence and 65% even when the resolver agrees with the ingestion
pick (evaluation/measure_resolver.py) — well below the ~85% needed to overwrite
safely. Auto-promoting would move ~40% of facts to WRONG leaves, re-polluting
exactly what section-filing protects. So the resolver only IMPROVES the
suggested leaf (52% vs ingestion's ~35%); the fact stays at the safe section and
Alex confirms via the one-click button (promote_chunk_to_leaf). Auto-promotion
awaits a better gate or a stronger classifier (both need the gold/correction
data to accumulate).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Confidence at/above which the resolver's pick is worth surfacing as the new
# suggested leaf. It is NOT a promotion gate — nothing is auto-promoted.
SUGGEST_MIN_CONFIDENCE = "high"


def _section_children(section_id: str) -> list[dict]:
    """The section node + its direct children as classify_fact_to_node candidates."""
    from web.handlers.feed_handler import _load_bp_architecture

    nodes = _load_bp_architecture()
    depth = section_id.count(".")
    out = []
    for n in nodes:
        nid = n.get("node_id", "")
        if nid == section_id or (nid.startswith(section_id + ".") and nid.count(".") == depth + 1):
            out.append({
                "node_id": nid,
                "node_title": n.get("node_title") or "",
                "similarity": 0.0,
                "level": n.get("level", 0),
                "purpose": (n.get("purpose") or "")[:250],
                "required_output": (n.get("required_output") or "")[:150],
                "prohibited_claims": (n.get("prohibited_claims_inference_patterns") or "")[:150],
                "parent_node": n.get("parent_node") or "",
            })
    return out


def resolve_text(content: str, section_id: str) -> dict:
    """Pure resolution (no DB): pick the best leaf for `content` within `section_id`.

    Returns the classify_fact_to_node result: {node_id, node_title, confidence,
    reasoning, none_fit}. Used both by resolve_chunk and by the precision
    measurement (evaluation/measure_resolver.py).
    """
    candidates = _section_children(section_id)
    if not candidates:
        return {"node_id": None, "confidence": "low", "none_fit": True,
                "reasoning": f"No children under {section_id}."}
    from web.handlers.llm_helper import classify_fact_to_node

    return classify_fact_to_node(content, candidates, use_fast_model=False)


def resolve_chunk(chunk_id: str, write: bool = True) -> dict:
    """Improve the suggested leaf for one provisional-section chunk.

    Re-resolves the leaf within the chunk's section and, when confident, writes
    it as the chunk's suggested_leaf (better than the ingestion-time suggestion).
    The chunk STAYS at the section — nothing is auto-promoted (see module docs).
    Alex confirms via promote_chunk_to_leaf.

    Args:
        chunk_id: the stored chunk.
        write: if False, compute the suggestion but don't write (dry run).

    Returns: {updated, from_section, suggested_leaf, confidence}.
    """
    try:
        from services.rag_service import _get_supabase, update_metadata

        sb = _get_supabase()
        row = sb.table("knowledge_base").select("content,metadata").eq(
            "id", chunk_id
        ).limit(1).execute()
        if not row.data:
            return {"updated": False, "error": "chunk not found"}
        meta = row.data[0].get("metadata") or {}
        content = row.data[0].get("content", "")
        section_id = meta.get("node_id")

        if not meta.get("filed_at_section"):
            return {"updated": False, "reason": "not a provisional-section chunk"}
        if meta.get("leaf_confirmed_by_alex"):
            return {"updated": False, "reason": "already confirmed by Alex"}

        r = resolve_text(content, section_id)
        leaf = r.get("node_id")
        conf = r.get("confidence")
        usable = (
            conf == SUGGEST_MIN_CONFIDENCE
            and leaf
            and not r.get("none_fit")
            and leaf != section_id  # a real leaf, deeper than the section
        )

        result = {
            "updated": False,
            "from_section": section_id,
            "suggested_leaf": leaf if usable else None,
            "confidence": conf,
        }
        if usable and write:
            leaf_title = next(
                (c["node_title"] for c in _section_children(section_id) if c["node_id"] == leaf),
                "",
            )
            update_metadata(chunk_id, {
                "suggested_leaf_id": leaf,
                "suggested_leaf_title": leaf_title,
                "leaf_suggested_by": "build",
                "leaf_suggested_at": datetime.now(timezone.utc).isoformat(),
            })
            result["updated"] = True
            logger.info("[LeafResolver] %s: refined suggestion %s (conf=%s)", chunk_id, leaf, conf)

        return result
    except Exception as e:  # noqa: BLE001
        logger.error("[LeafResolver] resolve_chunk failed for %s: %s", chunk_id, e)
        return {"updated": False, "error": str(e)}


def resolve_provisional(section_prefix: Optional[str] = None, limit: Optional[int] = None,
                        write: bool = True) -> dict:
    """Batch-refine suggested leaves for provisional facts.

    Called at Build time (scope to the section being assembled via
    section_prefix) or as a background pass. Only touches facts not yet
    build-suggested or Alex-confirmed. Returns counts.
    """
    from services.rag_service import _get_supabase

    sb = _get_supabase()
    rows = (sb.table("knowledge_base").select("id,metadata").contains(
        "metadata", {"filed_at_section": True}
    ).execute().data) or []

    considered = updated = 0
    for row in rows:
        meta = row.get("metadata") or {}
        if section_prefix and not str(meta.get("node_id", "")).startswith(section_prefix):
            continue
        if meta.get("leaf_confirmed_by_alex") or meta.get("leaf_suggested_by") == "build":
            continue
        considered += 1
        if resolve_chunk(row["id"], write=write).get("updated"):
            updated += 1
        if limit and considered >= limit:
            break

    logger.info("[LeafResolver] batch: considered=%d suggestions_updated=%d", considered, updated)
    return {"considered": considered, "updated": updated}
