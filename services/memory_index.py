"""
Memory Index — auto-links new chunks to related existing chunks.

When a fact is stored, this service finds semantically related chunks
and classifies the relationship (confirms, contradicts, updates, depends_on, related).
Writes to the chunk_relationships table.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

TABLE_NAME = "chunk_relationships"


def _get_supabase():
    """Lazy-load the Supabase client."""
    from services.rag_service import _get_supabase as get_sb
    return get_sb()


def _classify_relationship(
    similarity: float,
    new_meta: Optional[dict],
    existing_meta: Optional[dict],
) -> Optional[tuple[str, float]]:
    """Classify the relationship between two chunks.

    The old rules keyed 'contradicts'/'confirms' on epistemic_status == CONFIRMED,
    but the governance design deliberately never assigns CONFIRMED at ingestion —
    so those branches could never fire and every link fell to 'related'. This uses
    the fields that ARE populated at ingestion instead: verification_status,
    assertion_certainty, and content_type (from the audit metadata), plus node_id.

    Args:
        similarity: Cosine similarity between the two chunks.
        new_meta: Metadata dict of the newly stored chunk.
        existing_meta: Metadata dict of the existing chunk.

    Returns:
        Tuple of (relationship_type, confidence) or None if below threshold.
    """
    if similarity < 0.5:
        return None

    new_meta = new_meta or {}
    existing_meta = existing_meta or {}

    new_ac = new_meta.get("assertion_certainty")
    ex_ac = existing_meta.get("assertion_certainty")
    new_ct = new_meta.get("content_type")
    ex_ct = existing_meta.get("content_type")
    new_vs = new_meta.get("verification_status")
    ex_vs = existing_meta.get("verification_status")
    new_node_id = new_meta.get("node_id")
    existing_node_id = existing_meta.get("node_id")

    def _domain(nid: Optional[str]) -> Optional[str]:
        return ".".join(nid.split(".")[:2]) if nid else None

    # contradicts: two explicitly-asserted facts/decisions that are highly
    # similar but filed under DIFFERENT nodes in the SAME domain — i.e. two
    # firm claims about the same area that don't agree.
    if (
        similarity >= 0.82
        and new_ac == "explicit"
        and ex_ac == "explicit"
        and new_ct in ("fact", "decision")
        and ex_ct in ("fact", "decision")
        and new_node_id
        and existing_node_id
        and new_node_id != existing_node_id
        and _domain(new_node_id) == _domain(existing_node_id)
    ):
        return ("contradicts", similarity)

    # confirms: one chunk is verified and the other explicitly asserts the same
    # claim (same node) — the verified evidence backs the assertion.
    if (
        similarity >= 0.82
        and new_node_id
        and new_node_id == existing_node_id
        and (
            (new_vs == "verified" and ex_ac == "explicit")
            or (ex_vs == "verified" and new_ac == "explicit")
        )
    ):
        return ("confirms", similarity)

    if similarity >= 0.7 and new_node_id and existing_node_id:
        if (
            new_node_id.startswith(existing_node_id + ".")
            or existing_node_id.startswith(new_node_id + ".")
        ):
            return ("depends_on", similarity)

    if similarity >= 0.6 and new_node_id and existing_node_id:
        if new_node_id == existing_node_id:
            return ("updates", similarity)

    if similarity >= 0.5:
        return ("related", similarity)

    return None


def _relationship_exists(
    chunk_id_a: str, chunk_id_b: str, relationship_type: str
) -> bool:
    """Check if a relationship already exists between two chunks."""
    supabase = _get_supabase()
    result = (
        supabase.table(TABLE_NAME)
        .select("id")
        .eq("chunk_id_a", chunk_id_a)
        .eq("chunk_id_b", chunk_id_b)
        .eq("relationship_type", relationship_type)
        .limit(1)
        .execute()
    )
    if result.data:
        return True

    reverse = (
        supabase.table(TABLE_NAME)
        .select("id")
        .eq("chunk_id_a", chunk_id_b)
        .eq("chunk_id_b", chunk_id_a)
        .eq("relationship_type", relationship_type)
        .limit(1)
        .execute()
    )
    return bool(reverse.data)


def link_new_chunk(
    chunk_id: str,
    content: str,
    metadata: Optional[dict] = None,
    session_id: Optional[str] = None,
) -> dict:
    """Find and store relationships between a new chunk and existing chunks.

    Args:
        chunk_id: UUID of the newly stored chunk.
        content: Text content of the new chunk.
        metadata: Metadata dict of the new chunk (for node_id, epistemic_status).
        session_id: Session that created this chunk.

    Returns:
        Dict with: count (int), linked_nodes (list of node_ids from related
        chunks in different nodes), relationships (list of relationship dicts).
    """
    empty_result = {"count": 0, "linked_nodes": [], "relationships": []}
    try:
        from services.rag_service import retrieve

        metadata = metadata or {}
        new_node_id = metadata.get("node_id")

        similar_chunks = retrieve(
            query=content,
            top_k=20,
            threshold=0.3,
            recency_boost=False,
        )

        similar_chunks = [c for c in similar_chunks if c.id != chunk_id]

        if not similar_chunks:
            return empty_result

        supabase = _get_supabase()

        # The caller passes only a subset of the new chunk's metadata. The typed
        # relationship rules need its full audit fields (assertion_certainty,
        # content_type, verification_status), which were persisted when it was
        # stored — read them back so contradicts/confirms can actually fire.
        new_meta = dict(metadata)
        try:
            row = (
                supabase.table(TABLE_NAME)
                .select("metadata")
                .eq("id", chunk_id)
                .limit(1)
                .execute()
            )
            if row.data and row.data[0].get("metadata"):
                new_meta = row.data[0]["metadata"]
        except Exception as e:  # noqa: BLE001
            logger.debug("[MemoryIndex] could not load new chunk metadata: %s", e)

        created_count = 0
        linked_nodes = set()
        relationships = []

        for chunk in similar_chunks:
            existing_meta = chunk.metadata or {}
            existing_node_id = existing_meta.get("node_id")

            result = _classify_relationship(
                similarity=chunk.similarity,
                new_meta=new_meta,
                existing_meta=existing_meta,
            )

            if result is None:
                continue

            relationship_type, confidence = result

            if _relationship_exists(chunk_id, chunk.id, relationship_type):
                continue

            record = {
                "chunk_id_a": chunk_id,
                "chunk_id_b": chunk.id,
                "relationship_type": relationship_type,
                "confidence": round(confidence, 4),
                "session_id": session_id,
            }

            insert_result = supabase.table(TABLE_NAME).insert(record).execute()
            if insert_result.data:
                created_count += 1
                rel_info = {
                    "related_chunk_id": chunk.id,
                    "related_node_id": existing_node_id,
                    "relationship_type": relationship_type,
                    "confidence": round(confidence, 4),
                }
                relationships.append(rel_info)
                if existing_node_id and existing_node_id != new_node_id:
                    linked_nodes.add(existing_node_id)

        logger.info(
            "[MemoryIndex] Linked chunk %s: %d relationship(s) created, %d cross-node link(s)",
            chunk_id,
            created_count,
            len(linked_nodes),
        )
        return {
            "count": created_count,
            "linked_nodes": sorted(linked_nodes),
            "relationships": relationships,
        }

    except Exception as e:
        logger.error("[MemoryIndex] Error linking chunk %s: %s", chunk_id, e)
        return empty_result


def get_relationships(chunk_id: str) -> list[dict]:
    """Get all relationships for a given chunk.

    Args:
        chunk_id: UUID of the chunk.

    Returns:
        List of relationship dicts with: related_chunk_id, relationship_type, confidence, direction.
    """
    try:
        supabase = _get_supabase()

        as_a = (
            supabase.table(TABLE_NAME)
            .select("chunk_id_b, relationship_type, confidence")
            .eq("chunk_id_a", chunk_id)
            .execute()
        )

        as_b = (
            supabase.table(TABLE_NAME)
            .select("chunk_id_a, relationship_type, confidence")
            .eq("chunk_id_b", chunk_id)
            .execute()
        )

        relationships = []
        for row in (as_a.data or []):
            relationships.append({
                "related_chunk_id": row["chunk_id_b"],
                "relationship_type": row["relationship_type"],
                "confidence": row["confidence"],
                "direction": "outgoing",
            })
        for row in (as_b.data or []):
            relationships.append({
                "related_chunk_id": row["chunk_id_a"],
                "relationship_type": row["relationship_type"],
                "confidence": row["confidence"],
                "direction": "incoming",
            })

        return relationships

    except Exception as e:
        logger.error("[MemoryIndex] Error getting relationships for %s: %s", chunk_id, e)
        return []
