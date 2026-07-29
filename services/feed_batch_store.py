"""Storage for Feed review batches — Redis-backed, TTL'd, with an in-memory fallback.

Review cards are exactly what Redis is kept for in this project: ephemeral,
short-TTL workflow state whose loss is harmless. The facts a reviewer has
already confirmed are durable in `knowledge_base`; what lives here is the
*unreviewed* remainder of a pass, which previously sat in a module-level dict in
the web process and vanished on every restart or redeploy. Railway restarts
containers routinely, so a reviewer could lose an in-progress pass at any time.

See the datastore split in CLAUDE.md: durable records -> Supabase, ephemeral
short-TTL workflow state -> Redis. This is squarely the second.

**Fallback is loud, never silent.** If Redis is unmissing or unreachable the
store degrades to a process-local dict and logs a warning every time, because
the failure mode it is protecting against (lost cards on restart) is exactly
what the fallback reintroduces.

Upstash is a REST API with a per-request size limit, so oversized batches are
detected and kept in memory rather than being silently truncated or dropped.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

KEY_PREFIX = "feed:batch:"
INDEX_KEY = "feed:batches"

# A reviewer may come back to a queue across days; this is not a 10-minute
# pending-approval. Matches the quarantine TTL rather than the approval TTL.
DEFAULT_TTL_SECONDS = 7 * 24 * 3600

# Upstash rejects oversized REST payloads. A 36-card batch is ~80KB and a
# 170-card batch ~400KB, so real documents fit — but a very large upload could
# not, and must fail visibly rather than losing a reviewer's queue.
MAX_PAYLOAD_BYTES = 900_000

_memory: dict[str, dict] = {}
_redis_warned = False


def _redis():
    """Return the shared Upstash client, or None if it cannot be used."""
    global _redis_warned
    if not os.getenv("UPSTASH_REDIS_REST_URL") or not os.getenv(
        "UPSTASH_REDIS_REST_TOKEN"
    ):
        if not _redis_warned:
            logger.warning(
                "[FeedBatchStore] Upstash not configured — review batches are "
                "process-local and WILL be lost on restart"
            )
            _redis_warned = True
        return None
    try:
        from memory.redis_client import redis_client

        return redis_client
    except Exception as exc:  # noqa: BLE001 — degraded mode is announced
        if not _redis_warned:
            logger.warning(
                "[FeedBatchStore] Redis unavailable (%s) — review batches are "
                "process-local and WILL be lost on restart",
                str(exc)[:160],
            )
            _redis_warned = True
        return None


def save_batch(run_id: str, batch: dict[str, Any], ttl: int = DEFAULT_TTL_SECONDS) -> bool:
    """Persist a batch of review cards. Returns True when it reached Redis."""
    _memory[run_id] = batch
    client = _redis()
    if client is None:
        return False

    payload = json.dumps(batch)
    if len(payload) > MAX_PAYLOAD_BYTES:
        logger.error(
            "[FeedBatchStore] batch %s is %d bytes, over the %d limit — kept in "
            "memory only and WILL be lost on restart",
            run_id,
            len(payload),
            MAX_PAYLOAD_BYTES,
        )
        return False

    try:
        client.set(KEY_PREFIX + run_id, payload, ex=ttl)
        client.sadd(INDEX_KEY, run_id)
        client.expire(INDEX_KEY, ttl)
        return True
    except Exception as exc:  # noqa: BLE001 — logged, memory copy still serves
        logger.error(
            "[FeedBatchStore] failed to save batch %s: %s", run_id, str(exc)[:200]
        )
        return False


def get_batch(run_id: str) -> Optional[dict[str, Any]]:
    """Load a batch by run id, preferring Redis over the local copy."""
    client = _redis()
    if client is not None:
        try:
            raw = client.get(KEY_PREFIX + run_id)
            if raw:
                batch = json.loads(raw)
                _memory[run_id] = batch
                return batch
        except Exception as exc:  # noqa: BLE001 — fall through to memory
            logger.error(
                "[FeedBatchStore] failed to load batch %s: %s", run_id, str(exc)[:200]
            )
    return _memory.get(run_id)


def update_card(run_id: str, index: int, card: dict[str, Any]) -> bool:
    """Replace one card in a stored batch and write the batch back.

    Read-modify-write, not atomic. Two reviewers resolving the same card at the
    same instant could have one update overwrite the other's — acceptable here
    because the durable record (the row in knowledge_base) is written by
    confirm_card() independently and is unaffected. Revisit if Feed ever gets
    concurrent reviewers.
    """
    batch = get_batch(run_id)
    if not batch or "cards" not in batch:
        return False
    for position, existing in enumerate(batch["cards"]):
        if existing.get("index") == index:
            batch["cards"][position] = card
            break
    else:
        return False
    save_batch(run_id, batch)
    return True


def list_batches() -> list[dict[str, Any]]:
    """Summarise the batches currently held."""
    run_ids: list[str] = []
    client = _redis()
    if client is not None:
        try:
            run_ids = list(client.smembers(INDEX_KEY) or [])
        except Exception as exc:  # noqa: BLE001 — fall back to local keys
            logger.error("[FeedBatchStore] failed to list batches: %s", str(exc)[:200])
    if not run_ids:
        run_ids = list(_memory)

    out = []
    for run_id in run_ids:
        batch = get_batch(run_id)
        if not batch or "cards" not in batch:
            continue
        out.append(
            {
                "run_id": run_id,
                "source_document": batch.get("source_document"),
                "total_facts": batch.get("total_facts"),
                "resolved": sum(
                    1
                    for c in batch["cards"]
                    if c.get("status") != "awaiting_confirmation"
                ),
            }
        )
    return out


def delete_batch(run_id: str) -> None:
    """Drop a batch from both Redis and the local copy."""
    _memory.pop(run_id, None)
    client = _redis()
    if client is None:
        return
    try:
        client.delete(KEY_PREFIX + run_id)
        client.srem(INDEX_KEY, run_id)
    except Exception as exc:  # noqa: BLE001 — logged; the TTL will clean up
        logger.error(
            "[FeedBatchStore] failed to delete batch %s: %s", run_id, str(exc)[:200]
        )
