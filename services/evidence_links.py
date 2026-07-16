"""
Evidence Links — per-claim evidence boundaries.

The same fact can be:
  - SUFFICIENT to support "these 3 schools paid" in BP.6
  - INSUFFICIENT to support "the market will pay" in BP.9
  - BLOCKED from supporting "PMF proven" in BP.10

This service manages those per-link relationships so agents never
over-generalize from evidence that only supports narrow claims.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

TABLE_NAME = "evidence_links"


def _get_supabase():
    """Lazy-load the Supabase client."""
    from services.rag_service import _get_supabase as get_sb
    return get_sb()


def create_evidence_link(
    chunk_id: str,
    target_node_id: str,
    claim_supported: Optional[str] = None,
    claim_not_supported: Optional[str] = None,
    sufficiency_status: str = "untested",
    boundary_reason: Optional[str] = None,
    requires_corroboration: bool = False,
    session_id: Optional[str] = None,
) -> Optional[str]:
    """Create a per-link evidence boundary.

    Args:
        chunk_id: UUID of the evidence chunk.
        target_node_id: BP node this link targets.
        claim_supported: What this evidence CAN support in the target.
        claim_not_supported: What it CANNOT support despite being relevant.
        sufficiency_status: sufficient/partial/insufficient/untested/blocked.
        boundary_reason: Why this boundary exists.
        requires_corroboration: If True, cannot be used alone.
        session_id: Session that created this link.

    Returns:
        UUID of the created link, or None on failure.
    """
    try:
        supabase = _get_supabase()
        record = {
            "chunk_id": chunk_id,
            "target_node_id": target_node_id,
            "claim_supported": claim_supported,
            "claim_not_supported": claim_not_supported,
            "sufficiency_status": sufficiency_status,
            "boundary_reason": boundary_reason,
            "requires_corroboration": requires_corroboration,
            "session_id": session_id,
        }
        result = supabase.table(TABLE_NAME).insert(record).execute()
        if result.data:
            link_id = result.data[0]["id"]
            logger.info(
                "[EvidenceLinks] Created link: chunk %s → %s (%s)",
                chunk_id[:8], target_node_id, sufficiency_status,
            )
            return link_id
        return None
    except Exception as e:
        logger.error("[EvidenceLinks] Failed to create link: %s", e)
        return None


def get_links_for_chunk(chunk_id: str) -> list[dict]:
    """Get all evidence links for a given chunk."""
    try:
        supabase = _get_supabase()
        result = (
            supabase.table(TABLE_NAME)
            .select("*")
            .eq("chunk_id", chunk_id)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error("[EvidenceLinks] Error fetching links for %s: %s", chunk_id, e)
        return []


def get_links_for_node(target_node_id: str, sufficiency_filter: Optional[str] = None) -> list[dict]:
    """Get all evidence links targeting a given node.

    Args:
        target_node_id: The BP node to query.
        sufficiency_filter: Optional filter (e.g. "sufficient" to get only proven links).

    Returns:
        List of evidence link records.
    """
    try:
        supabase = _get_supabase()
        q = supabase.table(TABLE_NAME).select("*").eq("target_node_id", target_node_id)
        if sufficiency_filter:
            q = q.eq("sufficiency_status", sufficiency_filter)
        result = q.execute()
        return result.data or []
    except Exception as e:
        logger.error("[EvidenceLinks] Error fetching links for node %s: %s", target_node_id, e)
        return []


def update_link_sufficiency(
    link_id: str,
    sufficiency_status: str,
    boundary_reason: Optional[str] = None,
    controller_approved: bool = False,
    controller_decision_id: Optional[str] = None,
) -> bool:
    """Update a link's sufficiency status (typically by controller decision)."""
    try:
        supabase = _get_supabase()
        update_data = {
            "sufficiency_status": sufficiency_status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "controller_approved": controller_approved,
        }
        if boundary_reason:
            update_data["boundary_reason"] = boundary_reason
        if controller_decision_id:
            update_data["controller_decision_id"] = controller_decision_id

        result = (
            supabase.table(TABLE_NAME)
            .update(update_data)
            .eq("id", link_id)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        logger.error("[EvidenceLinks] Failed to update link %s: %s", link_id, e)
        return False


def create_links_from_multi_node(
    chunk_id: str,
    primary_node_id: str,
    secondary_nodes: list[dict],
    content_type: str,
    evidence_tier: str,
    session_id: Optional[str] = None,
) -> int:
    """Auto-create evidence links from multi-node linking results.

    For each secondary node, creates a link with appropriate boundaries
    based on evidence tier and relationship type.

    Args:
        chunk_id: UUID of the stored chunk.
        primary_node_id: Where the fact is primarily stored.
        secondary_nodes: From multi_node_linker.derive_secondary_nodes().
        content_type: Classified content type.
        evidence_tier: E0-E4 from source_reliability.
        session_id: Session.

    Returns:
        Number of links created.
    """
    if not secondary_nodes:
        return 0

    created = 0
    for node in secondary_nodes:
        node_id = node["node_id"]
        rel_type = node.get("relationship_type", "related")

        if evidence_tier in ("E0", "E1"):
            sufficiency = "insufficient"
            requires_corroboration = True
            reason = f"Evidence tier {evidence_tier} — requires corroboration before use"
        elif rel_type == "confirms":
            sufficiency = "partial"
            requires_corroboration = False
            reason = f"Confirms existing evidence in {node_id}"
        elif rel_type == "depends_on":
            sufficiency = "partial"
            requires_corroboration = False
            reason = f"Dependency relationship with {node_id}"
        else:
            sufficiency = "untested"
            requires_corroboration = True
            reason = f"Cross-node relevance ({rel_type}) — sufficiency not assessed"

        link_id = create_evidence_link(
            chunk_id=chunk_id,
            target_node_id=node_id,
            claim_supported=None,
            claim_not_supported=None,
            sufficiency_status=sufficiency,
            boundary_reason=reason,
            requires_corroboration=requires_corroboration,
            session_id=session_id,
        )
        if link_id:
            created += 1

    logger.info(
        "[EvidenceLinks] Created %d per-link boundaries for chunk %s",
        created, chunk_id[:8],
    )
    return created
