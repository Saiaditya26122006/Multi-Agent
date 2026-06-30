"""
Temporal Decay — manages freshness scoring and staleness of RAG chunks.

Ensures retrieval prefers recent confirmed data over old unconfirmed data.
Provides utilities for:
- Checking if a chunk is stale
- Confirming a chunk is still valid (resets staleness)
- Applying decay weights during retrieval scoring
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_STALE_DAYS = 90

FRESHNESS_POLICIES = {
    "stale_after_30_days": 30,
    "stale_after_60_days": 60,
    "stale_after_90_days": 90,
    "stale_after_180_days": 180,
    "never_stale": None,
}


def is_stale(
    created_at: str,
    freshness_policy: Optional[str] = None,
    last_confirmed: Optional[str] = None,
) -> bool:
    """Check if a chunk is stale based on its age and freshness policy.

    Args:
        created_at: ISO timestamp of when the chunk was created.
        freshness_policy: Policy string (e.g., "stale_after_90_days").
        last_confirmed: ISO timestamp of last confirmation (overrides created_at).

    Returns:
        True if the chunk is stale.
    """
    if freshness_policy == "never_stale":
        return False

    stale_days = FRESHNESS_POLICIES.get(freshness_policy, DEFAULT_STALE_DAYS)
    if stale_days is None:
        return False

    reference_time = last_confirmed or created_at
    if not reference_time:
        return True

    try:
        ref_dt = datetime.fromisoformat(reference_time.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age = now - ref_dt
        return age.days > stale_days
    except (ValueError, TypeError):
        return False


def compute_recency_score(
    created_at: str,
    last_confirmed: Optional[str] = None,
    half_life_days: int = 180,
) -> float:
    """Compute a recency score (0.0-1.0) using exponential decay.

    Args:
        created_at: ISO timestamp of chunk creation.
        last_confirmed: ISO timestamp of last confirmation (used if newer).
        half_life_days: Days after which score drops to 0.5.

    Returns:
        Float between 0.0 (very old) and 1.0 (just created/confirmed).
    """
    reference_time = last_confirmed or created_at
    if not reference_time:
        return 0.5

    try:
        ref_dt = datetime.fromisoformat(reference_time.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_days = max(0, (now - ref_dt).days)

        import math
        decay_rate = math.log(2) / half_life_days
        score = math.exp(-decay_rate * age_days)
        return round(max(0.0, min(1.0, score)), 4)
    except (ValueError, TypeError):
        return 0.5


def compute_status_weight(epistemic_status: Optional[str]) -> float:
    """Compute a weight multiplier based on epistemic status.

    CONFIRMED data is weighted highest, ASSUMPTION lower, etc.

    Returns:
        Float multiplier (0.3-1.0).
    """
    weights = {
        "CONFIRMED": 1.0,
        "ASSUMPTION": 0.7,
        "INFERRED": 0.6,
        "CONTRADICTION": 0.5,
        "MISSING": 0.4,
        "UNVERIFIED_EXTERNAL_CLAIM": 0.5,
        "SUPERSEDED": 0.1,
    }
    return weights.get(epistemic_status, 0.6)


def compute_final_score(
    similarity: float,
    created_at: Optional[str] = None,
    last_confirmed: Optional[str] = None,
    epistemic_status: Optional[str] = None,
    freshness_policy: Optional[str] = None,
    similarity_weight: float = 0.6,
    recency_weight: float = 0.25,
    status_weight: float = 0.15,
) -> float:
    """Compute a combined retrieval score from similarity, recency, and status.

    Formula:
        final = (similarity * sim_w) + (recency * rec_w) + (status * stat_w)

    Args:
        similarity: Cosine similarity from vector search (0.0-1.0).
        created_at: ISO timestamp.
        last_confirmed: ISO timestamp of last confirmation.
        epistemic_status: Status tag.
        freshness_policy: Staleness policy.
        similarity_weight: Weight for similarity component.
        recency_weight: Weight for recency component.
        status_weight: Weight for status component.

    Returns:
        Combined score (0.0-1.0).
    """
    if freshness_policy and is_stale(created_at or "", freshness_policy, last_confirmed):
        similarity *= 0.5

    recency = compute_recency_score(created_at or "", last_confirmed)
    status = compute_status_weight(epistemic_status)

    final = (
        similarity * similarity_weight
        + recency * recency_weight
        + status * status_weight
    )

    return round(min(1.0, max(0.0, final)), 4)


def confirm_chunk(chunk_id: str) -> bool:
    """Mark a chunk as re-confirmed (resets staleness clock).

    Args:
        chunk_id: UUID of the chunk to confirm.

    Returns:
        True if successful.
    """
    import os
    from dotenv import load_dotenv
    load_dotenv()
    from supabase import create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    client = create_client(url, key)

    now = datetime.now(timezone.utc).isoformat()
    result = (
        client.table("knowledge_base")
        .update({"last_confirmed": now})
        .eq("id", chunk_id)
        .execute()
    )

    if result.data:
        logger.info("[Temporal] Confirmed chunk %s at %s", chunk_id, now)
        return True

    logger.warning("[Temporal] Failed to confirm chunk %s", chunk_id)
    return False


def flag_stale_chunks(dry_run: bool = True) -> list[dict]:
    """Scan for chunks that have exceeded their freshness policy.

    Args:
        dry_run: If True, only report stale chunks without modifying them.

    Returns:
        List of stale chunk summaries.
    """
    import os
    from dotenv import load_dotenv
    load_dotenv()
    from supabase import create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    client = create_client(url, key)

    result = (
        client.table("knowledge_base")
        .select("id, content, created_at, last_confirmed, freshness_policy, epistemic_status")
        .not_.is_("freshness_policy", "null")
        .is_("superseded_by", "null")
        .execute()
    )

    stale_chunks = []
    for row in result.data or []:
        if is_stale(
            row.get("created_at", ""),
            row.get("freshness_policy"),
            row.get("last_confirmed"),
        ):
            stale_chunks.append({
                "id": row["id"],
                "content_preview": row.get("content", "")[:80],
                "freshness_policy": row.get("freshness_policy"),
                "created_at": row.get("created_at"),
                "last_confirmed": row.get("last_confirmed"),
            })

    logger.info(
        "[Temporal] Found %d stale chunks (dry_run=%s)",
        len(stale_chunks),
        dry_run,
    )

    return stale_chunks
