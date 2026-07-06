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
    new_epistemic: Optional[str],
    existing_epistemic: Optional[str],
    new_node_id: Optional[str],
    existing_node_id: Optional[str],
) -> Optional[tuple[str, float]]:
    """Classify the relationship between two chunks.

    Args:
        similarity: Cosine similarity between the two chunks.
        new_epistemic: Epistemic status of the new chunk.
        existing_epistemic: Epistemic status of the existing chunk.
        new_node_id: BP node_id of the new chunk.
        existing_node_id: BP node_id of the existing chunk.

    Returns:
        Tuple of (relationship_type, confidence) or None if below threshold.
    """
    if similarity < 0.5:
        return None

    if similarity >= 0.92 and new_epistemic == existing_epistemic:
        return ("related", similarity)

    if similarity >= 0.85 and new_epistemic != existing_epistemic:
        pair = {new_epistemic, existing_epistemic}
        if "CONFIRMED" in pair and "CONTRADICTION" in pair:
            return ("contradicts", similarity)
        if "CONFIRMED" in pair and "ASSUMPTION" in pair:
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
) -> int:
    """Find and store relationships between a new chunk and existing chunks.

    Args:
        chunk_id: UUID of the newly stored chunk.
        content: Text content of the new chunk.
        metadata: Metadata dict of the new chunk (for node_id, epistemic_status).
        session_id: Session that created this chunk.

    Returns:
        Number of relationships created.
    """
    try:
        from services.rag_service import retrieve

        metadata = metadata or {}
        new_epistemic = metadata.get("epistemic_status")
        new_node_id = metadata.get("node_id")

        similar_chunks = retrieve(
            query=content,
            top_k=20,
            threshold=0.3,
            recency_boost=False,
        )

        similar_chunks = [c for c in similar_chunks if c.id != chunk_id]

        if not similar_chunks:
            return 0

        supabase = _get_supabase()
        created_count = 0

        for chunk in similar_chunks:
            existing_epistemic = chunk.epistemic_status
            existing_node_id = chunk.metadata.get("node_id") if chunk.metadata else None

            result = _classify_relationship(
                similarity=chunk.similarity,
                new_epistemic=new_epistemic,
                existing_epistemic=existing_epistemic,
                new_node_id=new_node_id,
                existing_node_id=existing_node_id,
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

        logger.info(
            "[MemoryIndex] Linked chunk %s: %d relationship(s) created",
            chunk_id,
            created_count,
        )
        return created_count

    except Exception as e:
        logger.error("[MemoryIndex] Error linking chunk %s: %s", chunk_id, e)
        return 0


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
