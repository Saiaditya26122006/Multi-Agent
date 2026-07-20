"""Augmented BP-node index maintenance.

The Feed classifier's match_bp_node() retrieves candidate nodes from a
knowledge_base layer that embeds richer node text ("title. purpose.
required_output"). Measured (evaluation/compare_indexes.py): this lifts
exact-node retrieval 45%->68% and section retrieval 50%->75% vs the plain
bp_architecture layer.

This module owns that layer so it stays in sync:
  - reindex_all(): full rebuild (scripts/ingest_bp_aug).
  - index_node(node): upsert ONE node — called by the Feed handler the moment
    Alex creates a new node, so it is immediately retrievable instead of
    silently missing until the next full rebuild.

Stored under source_type 'ceo_doc' with metadata.layer='bp_architecture_aug'
(the DB knowledge_base_source_type_check constraint rejects 'ssot_node').
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

AUG_LAYER = "bp_architecture_aug"
_ARCH_PATH = Path(__file__).parent.parent / "ceo_data" / "bp_architecture.json"


def aug_text(node: dict) -> str:
    """Richer node text that tested at 68% exact / 75% section recall."""
    title = node.get("node_title") or ""
    purpose = node.get("purpose") or ""
    required = node.get("required_output") or ""
    return f"{title}. {purpose}. {required}".strip()


def delete_node(node_id: str) -> int:
    """Remove any existing aug rows for a node_id. Returns count deleted."""
    from services.rag_service import _get_supabase

    sb = _get_supabase()
    existing = sb.table("knowledge_base").select("id").contains(
        "metadata", {"layer": AUG_LAYER, "node_id": node_id}
    ).execute()
    rows = existing.data or []
    for row in rows:
        sb.table("knowledge_base").delete().eq("id", row["id"]).execute()
    return len(rows)


def index_node(node: dict) -> Optional[str]:
    """Upsert one node into the aug layer (delete-then-insert = idempotent).

    Safe to call from request paths: logs and returns None on failure rather
    than raising, so a transient index error never blocks node creation.
    """
    node_id = node.get("node_id")
    if not node_id or not str(node_id).startswith("BP."):
        return None
    text = aug_text(node)
    if not text or text == "..":
        return None
    try:
        from services.rag_service import store

        delete_node(node_id)  # drop stale copy if the node was edited
        chunk_id = store(
            content=text,
            source_type="ceo_doc",
            section=node_id.split(".")[1] if "." in node_id else None,
            topic_tags=["bp-node-aug", node_id],
            metadata={
                "layer": AUG_LAYER,
                "node_id": node_id,
                "node_title": node.get("node_title", ""),
                "level": node.get("level"),
            },
            deduplicate=False,
        )
        logger.info("[AugIndex] Indexed node %s into aug layer", node_id)
        return chunk_id
    except Exception as e:  # noqa: BLE001
        logger.warning("[AugIndex] Failed to index node %s: %s", node_id, e)
        return None


def reindex_all() -> dict:
    """Full rebuild: clear the aug layer and re-embed every architecture node."""
    from services.rag_service import _get_supabase

    arch = json.loads(_ARCH_PATH.read_text())
    nodes = [n for n in arch.get("nodes", []) if str(n.get("node_id", "")).startswith("BP.")]

    sb = _get_supabase()
    existing = sb.table("knowledge_base").select("id").contains(
        "metadata", {"layer": AUG_LAYER}
    ).execute()
    for row in (existing.data or []):
        sb.table("knowledge_base").delete().eq("id", row["id"]).execute()
    if existing.data:
        logger.info("[AugIndex] Cleared %d existing aug rows", len(existing.data))

    from services.rag_service import store

    ingested = 0
    for node in nodes:
        text = aug_text(node)
        if not text or text == "..":
            continue
        nid = node["node_id"]
        cid = store(
            content=text,
            source_type="ceo_doc",
            section=nid.split(".")[1] if "." in nid else None,
            topic_tags=["bp-node-aug", nid],
            metadata={
                "layer": AUG_LAYER,
                "node_id": nid,
                "node_title": node.get("node_title", ""),
                "level": node.get("level"),
            },
            deduplicate=False,
        )
        if cid:
            ingested += 1
        if ingested % 100 == 0:
            logger.info("[AugIndex]   %d/%d", ingested, len(nodes))

    logger.info("[AugIndex] Rebuilt aug layer: %d nodes", ingested)
    return {"total": len(nodes), "ingested": ingested}
