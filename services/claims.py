"""First-class claims service (Alex audit issue #3).

A claim is an atomic assertion attached to a BP node. Evidence links reference a
claim via claim_id so sufficiency can be aggregated per claim instead of scattered
across free-text candidate_claim strings.

Requires the add_claims.sql migration (apply once via the Supabase SQL Editor).
Every function degrades gracefully (logs + returns a safe default) if the table
is not present yet, so importing/calling this never crashes the app.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

TABLE = "claims"


def _sb():
    from services.rag_service import _get_supabase

    return _get_supabase()


def _claim_key(text: str) -> str:
    """Normalized claim text for dedup within a node."""
    return " ".join((text or "").lower().split())


def create_claim(
    node_id: str,
    claim_text: str,
    status: str = "open",
    session_id: Optional[str] = None,
) -> Optional[str]:
    """Create (or return existing) a claim for a node. Returns claim id or None."""
    key = _claim_key(claim_text)
    try:
        sb = _sb()
        existing = (
            sb.table(TABLE).select("id").eq("node_id", node_id).eq("claim_key", key).limit(1).execute().data
        )
        if existing:
            return existing[0]["id"]
        row = sb.table(TABLE).insert({
            "node_id": node_id,
            "claim_text": claim_text,
            "claim_key": key,
            "status": status,
            "source_session_id": session_id,
        }).execute()
        return row.data[0]["id"] if row.data else None
    except Exception as e:
        logger.error("[claims] create_claim failed (migration applied?): %s", e)
        return None


def get_claims_for_node(node_id: str) -> list[dict]:
    """Return all claims for a node."""
    try:
        return _sb().table(TABLE).select("*").eq("node_id", node_id).execute().data or []
    except Exception as e:
        logger.error("[claims] get_claims_for_node failed: %s", e)
        return []


def link_evidence_to_claim(evidence_link_id: str, claim_id: str) -> bool:
    """Point an evidence_links row at a first-class claim."""
    try:
        _sb().table("evidence_links").update({"claim_id": claim_id}).eq("id", evidence_link_id).execute()
        return True
    except Exception as e:
        logger.error("[claims] link_evidence_to_claim failed: %s", e)
        return False


def set_claim_status(claim_id: str, status: str, approved_version: Optional[int] = None) -> bool:
    """Controller action: set a claim's status (and optionally bump approved_version)."""
    try:
        from datetime import datetime, timezone

        patch = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
        if approved_version is not None:
            patch["approved_version"] = approved_version
        _sb().table(TABLE).update(patch).eq("id", claim_id).execute()
        return True
    except Exception as e:
        logger.error("[claims] set_claim_status failed: %s", e)
        return False


def backfill_claims_from_evidence_links() -> int:
    """One-off: create claim rows from existing evidence_links.candidate_claim and
    link them. Idempotent (create_claim dedups per node). Returns links updated.

    Run after applying add_claims.sql."""
    updated = 0
    try:
        sb = _sb()
        links = sb.table("evidence_links").select("id,target_node_id,candidate_claim,claim_id").execute().data or []
        for link in links:
            if link.get("claim_id") or not link.get("candidate_claim"):
                continue
            cid = create_claim(link["target_node_id"], link["candidate_claim"])
            if cid and link_evidence_to_claim(link["id"], cid):
                updated += 1
    except Exception as e:
        logger.error("[claims] backfill failed: %s", e)
    return updated
