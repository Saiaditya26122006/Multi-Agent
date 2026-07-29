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

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

AUG_LAYER = "bp_architecture_aug"
_ARCH_PATH = Path(__file__).parent.parent / "ceo_data" / "bp_architecture.json"
_HASH_MARKER = Path(__file__).parent.parent / "ceo_data" / ".aug_index_hash"


def _arch_hash() -> str:
    """SHA-256 of bp_architecture.json (empty string if missing)."""
    if not _ARCH_PATH.exists():
        return ""
    return hashlib.sha256(_ARCH_PATH.read_bytes()).hexdigest()


def mark_synced() -> None:
    """Record that the aug layer reflects the current architecture file.

    Called after a full rebuild and after each incremental index_node() from
    Feed node creation, so a direct edit to bp_architecture.json is the only
    thing that makes ensure_fresh() see the layer as stale.
    """
    try:
        _HASH_MARKER.write_text(_arch_hash())
    except Exception as e:  # noqa: BLE001
        logger.warning("[AugIndex] Could not write hash marker: %s", e)


def _aug_layer_empty() -> bool:
    from services.rag_service import _get_supabase

    try:
        r = _get_supabase().table("knowledge_base").select(
            "id", count="exact"
        ).contains("metadata", {"layer": AUG_LAYER}).limit(1).execute()
        return (r.count or 0) == 0
    except Exception as e:  # noqa: BLE001
        logger.warning("[AugIndex] Could not check aug layer size: %s", e)
        return False


def is_stale() -> bool:
    """True if the aug layer is empty or the architecture file changed since sync."""
    if _aug_layer_empty():
        return True
    stored = _HASH_MARKER.read_text().strip() if _HASH_MARKER.exists() else ""
    return stored != _arch_hash()


def ensure_fresh(force: bool = False) -> dict:
    """Rebuild the aug layer if it is stale (or force=True). Safe to call at
    startup — returns a status dict, never raises."""
    try:
        if not force and not is_stale():
            return {"rebuilt": False, "reason": "fresh"}
        logger.info("[AugIndex] Aug layer stale — rebuilding")
        result = reindex_all()
        result["rebuilt"] = True
        return result
    except Exception as e:  # noqa: BLE001
        logger.error("[AugIndex] ensure_fresh failed: %s", e)
        return {"rebuilt": False, "error": str(e)}


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
        result = store(
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
        return result.id
    except Exception as e:  # noqa: BLE001
        # Per-node: one bad node must not abort a rebuild, but it is an error.
        logger.error("[AugIndex] Failed to index node %s: %s", node_id, e)
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
        result = store(
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
        if result:
            ingested += 1
        if ingested % 100 == 0:
            logger.info("[AugIndex]   %d/%d", ingested, len(nodes))

    mark_synced()
    logger.info("[AugIndex] Rebuilt aug layer: %d nodes", ingested)
    return {"total": len(nodes), "ingested": ingested}
