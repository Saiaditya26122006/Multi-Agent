"""
Multi-Node Linker — derives secondary_node_ids and evidence_use_boundary
after a chunk is stored and its relationships are computed.

Solves the "evidence trapped in one node" problem: when a fact informs
multiple BP architecture nodes, this service writes cross-node references
into the chunk's metadata so agents working on ANY related node can find it.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

EVIDENCE_USE_BOUNDARIES = {
    "cite_freely": "Can be referenced by any agent working on any related node",
    "cite_with_caveat": "Can be referenced but must note it's stored under a different node",
    "primary_only": "Should only be used by agents working on the primary node",
}

CROSS_NODE_RELATIONSHIP_TYPES = {"confirms", "related", "updates", "depends_on"}


def derive_secondary_nodes(
    primary_node_id: str,
    link_result: dict,
    min_confidence: float = 0.5,
) -> list[dict]:
    """Derive secondary node IDs from relationship linkage results.

    Args:
        primary_node_id: The node this chunk is stored under.
        link_result: Dict from link_new_chunk() with linked_nodes and relationships.
        min_confidence: Minimum relationship confidence to include.

    Returns:
        List of dicts: {node_id, relationship_type, confidence}.
    """
    if not link_result or not link_result.get("relationships"):
        return []

    secondary = {}
    for rel in link_result["relationships"]:
        node_id = rel.get("related_node_id")
        if not node_id or node_id == primary_node_id:
            continue
        if rel["confidence"] < min_confidence:
            continue
        if rel["relationship_type"] not in CROSS_NODE_RELATIONSHIP_TYPES:
            continue

        if node_id not in secondary or rel["confidence"] > secondary[node_id]["confidence"]:
            secondary[node_id] = {
                "node_id": node_id,
                "relationship_type": rel["relationship_type"],
                "confidence": rel["confidence"],
            }

    return sorted(secondary.values(), key=lambda x: x["confidence"], reverse=True)


def determine_evidence_use_boundary(
    content_type: str,
    epistemic_status: str,
    secondary_count: int,
) -> str:
    """Determine how freely this evidence can be cited across nodes.

    Args:
        content_type: The classified content type (decision, risk, metric, etc.).
        epistemic_status: CONFIRMED, ASSUMPTION, INFERRED, etc.
        secondary_count: Number of secondary nodes this fact links to.

    Returns:
        One of: "cite_freely", "cite_with_caveat", "primary_only".
    """
    if epistemic_status == "CONFIRMED" and content_type in ("fact", "metric", "decision"):
        return "cite_freely"

    if epistemic_status == "ASSUMPTION" or content_type in ("assumption", "open_question"):
        return "cite_with_caveat"

    if epistemic_status in ("CONTRADICTION", "MISSING"):
        return "primary_only"

    if secondary_count >= 2 and epistemic_status in ("CONFIRMED", "INFERRED"):
        return "cite_freely"

    if secondary_count >= 1:
        return "cite_with_caveat"

    return "primary_only"


def compute_multi_node_metadata(
    chunk_id: str,
    primary_node_id: str,
    content_type: str,
    epistemic_status: str,
    link_result: dict,
) -> Optional[dict]:
    """Compute and write multi-node linking metadata to a stored chunk.

    Called after link_new_chunk() returns. Derives secondary_node_ids,
    evidence_use_boundary, and writes them to the chunk's metadata via
    rag_service.update_metadata().

    Args:
        chunk_id: UUID of the stored chunk.
        primary_node_id: Node the chunk is stored under.
        content_type: Classified content type.
        epistemic_status: Epistemic status of the chunk.
        link_result: Dict from link_new_chunk().

    Returns:
        The multi-node metadata dict that was written, or None if nothing to write.
    """
    if not chunk_id or not link_result:
        return None

    secondary = derive_secondary_nodes(primary_node_id or "", link_result)
    secondary_ids = [s["node_id"] for s in secondary]

    boundary = determine_evidence_use_boundary(
        content_type=content_type,
        epistemic_status=epistemic_status,
        secondary_count=len(secondary_ids),
    )

    if not secondary_ids and boundary == "primary_only":
        return None

    multi_node_meta = {
        "primary_node_id": primary_node_id,
        "secondary_node_ids": secondary_ids,
        "secondary_relationships": secondary[:5],
        "evidence_use_boundary": boundary,
        "cross_node_count": len(secondary_ids),
    }

    try:
        from services.rag_service import update_metadata

        update_metadata(chunk_id, multi_node_meta)
        logger.info(
            "[MultiNodeLinker] Chunk %s: %d secondary node(s), boundary=%s",
            chunk_id,
            len(secondary_ids),
            boundary,
        )
    except Exception as e:
        logger.error("[MultiNodeLinker] Failed to write metadata for chunk %s: %s", chunk_id, e)

    return multi_node_meta
