"""
RAG Service — embed, store, retrieve knowledge chunks via Supabase pgvector.

Provides the core retrieval-augmented generation layer for the multi-agent system.
All CEO data, conversations, decisions, and agent insights flow through here.
"""

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_supabase_client = None

EMBEDDING_DIM = 1024
DEFAULT_TOP_K = 5
DEFAULT_THRESHOLD = 0.3
TABLE_NAME = "knowledge_base"

VALID_SOURCE_TYPES = {
    "ceo_doc",
    "conversation",
    "decision",
    "feedback",
    "correction",
    "agent_insight",
    "negative_knowledge",
    "preference_pattern",
    "external_research",
    "assumption_lifecycle",
    "contradiction_resolution",
    "run_metadata",
    # NOTE: keep this set in sync with the DB CHECK constraint in
    # database/migrations/add_knowledge_base.sql. ssot_node/ssot_mapping/
    # bp_architecture/ssot_alex were advertised here but REJECTED by the DB
    # constraint (a silent trap — store() would fail at insert). They are unused
    # as source_types; BP-architecture retrieval uses source_type='ceo_doc' with
    # metadata.layer='bp_architecture'/'bp_architecture_aug' instead.
}

VALID_EPISTEMIC_STATUSES = {
    "CONFIRMED",
    "ASSUMPTION",
    "CONTRADICTION",
    "INFERRED",
    "MISSING",
    "SUPERSEDED",
    "UNVERIFIED_EXTERNAL_CLAIM",
}


class StoreOutcome(str, Enum):
    """Why a store()/batch_store() call ended the way it did.

    STORED is the only outcome that means a new row exists. The SKIPPED_*
    outcomes are legitimate no-ops, not failures. FAILED is produced only by
    batch_store(); store() raises RagStoreError instead.
    """

    STORED = "stored"
    SKIPPED_EMPTY = "skipped_empty"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    SKIPPED_INVALID_TYPE = "skipped_invalid_type"
    FAILED = "failed"


@dataclass(frozen=True)
class StoreResult:
    """Outcome of a single store attempt.

    Truthy only when a new row was written, so `if result:` keeps the meaning
    that `if chunk_id:` had before this type existed.
    """

    outcome: StoreOutcome
    id: Optional[str] = None
    duplicate_of: Optional[str] = None
    error: Optional[str] = None

    def __bool__(self) -> bool:
        """True only for STORED — a skip or a failure is not a write."""
        return self.outcome is StoreOutcome.STORED


class RagStoreError(RuntimeError):
    """An insert was attempted and did not produce a row.

    Raised by store() when Supabase accepts the request but returns no data
    (RLS rejection, swallowed constraint violation). Callers must not treat
    this as a skip.
    """


@dataclass
class Chunk:
    """A retrieved knowledge chunk with metadata."""

    id: str
    content: str
    source_type: str
    similarity: float
    section: Optional[str] = None
    epistemic_status: Optional[str] = None
    topic_tags: list = field(default_factory=list)
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    agent_name: Optional[str] = None
    confidence: Optional[float] = None
    metadata: dict = field(default_factory=dict)
    created_at: Optional[str] = None


def _get_supabase():
    """Lazy-load the Supabase client (singleton)."""
    global _supabase_client
    if _supabase_client is None:
        from supabase import create_client

        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY")
        if not url or not key:
            raise ValueError(
                "[RAG] SUPABASE_URL and SUPABASE_KEY (or SUPABASE_ANON_KEY) must be set in .env"
            )
        _supabase_client = create_client(url, key)
        logger.info("[RAG] Supabase client initialized")
    return _supabase_client


def embed(text: str, input_type: str = "search_query") -> list[float]:
    """Convert text into a 1024-dimensional embedding vector via Amazon Titan Embed v2.

    Args:
        text: The text to embed.
        input_type: "search_document" for indexing, "search_query" for retrieval.

    Returns:
        List of 1024 floats representing the embedding.
    """
    from services.embedding_service import embed as cohere_embed

    return cohere_embed(text, input_type=input_type)


def content_hash(content: str) -> str:
    """Generate SHA-256 hash of content for deduplication."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def store(
    content: str,
    source_type: str,
    section: Optional[str] = None,
    epistemic_status: Optional[str] = None,
    topic_tags: Optional[list[str]] = None,
    session_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    confidence: Optional[float] = None,
    freshness_policy: Optional[str] = None,
    metadata: Optional[dict] = None,
    deduplicate: bool = True,
) -> StoreResult:
    """Embed and store a content chunk in the knowledge base.

    Args:
        content: The text content to store.
        source_type: One of the valid source types.
        section: Business plan section number (e.g., "8", "12").
        epistemic_status: CONFIRMED, ASSUMPTION, etc.
        topic_tags: List of searchable topic tags.
        session_id: Session this chunk belongs to.
        run_id: Pipeline run this chunk belongs to.
        agent_name: Agent that produced this chunk.
        confidence: Confidence score (0.0-1.0).
        freshness_policy: e.g., "stale_after_90_days".
        metadata: Additional JSON metadata.
        deduplicate: If True, skip insert if identical content exists.

    Returns:
        StoreResult — STORED with .id when a row was written, SKIPPED_EMPTY for
        blank content, SKIPPED_DUPLICATE with .duplicate_of when the identical
        content is already stored.

    Raises:
        ValueError: Invalid source_type or epistemic_status.
        RagStoreError: The insert ran but produced no row.
    """
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(
            f"[RAG] Invalid source_type '{source_type}'. "
            f"Must be one of: {VALID_SOURCE_TYPES}"
        )

    if epistemic_status and epistemic_status not in VALID_EPISTEMIC_STATUSES:
        raise ValueError(
            f"[RAG] Invalid epistemic_status '{epistemic_status}'. "
            f"Must be one of: {VALID_EPISTEMIC_STATUSES}"
        )

    if not content or not content.strip():
        logger.warning("[RAG] Skipping empty content")
        return StoreResult(StoreOutcome.SKIPPED_EMPTY)

    c_hash = content_hash(content)

    if deduplicate:
        supabase = _get_supabase()
        existing = (
            supabase.table(TABLE_NAME)
            .select("id")
            .eq("metadata->>content_hash", c_hash)
            .limit(1)
            .execute()
        )
        if existing.data:
            existing_id = existing.data[0]["id"]
            logger.debug("[RAG] Deduplicated — content already exists: %s", existing_id)
            return StoreResult(
                StoreOutcome.SKIPPED_DUPLICATE, duplicate_of=existing_id
            )

    embedding = embed(content, input_type="search_document")

    record = {
        "content": content,
        "embedding": embedding,
        "source_type": source_type,
        "section": section,
        "epistemic_status": epistemic_status,
        "topic_tags": topic_tags or [],
        "session_id": session_id,
        "run_id": run_id,
        "agent_name": agent_name,
        "confidence": confidence,
        "freshness_policy": freshness_policy,
        "metadata": {**(metadata or {}), "content_hash": c_hash},
    }

    supabase = _get_supabase()
    result = supabase.table(TABLE_NAME).insert(record).execute()

    if result.data:
        chunk_id = result.data[0]["id"]
        logger.info(
            "[RAG] Stored chunk: id=%s, type=%s, section=%s, status=%s",
            chunk_id,
            source_type,
            section,
            epistemic_status,
        )
        return StoreResult(StoreOutcome.STORED, id=chunk_id)

    # Supabase accepted the request but returned no row. Never silent: the
    # content would otherwise vanish with only a log line behind it.
    detail = (
        f"[RAG] Insert returned no data — chunk NOT stored. "
        f"source_type={source_type}, section={section}, "
        f"epistemic_status={epistemic_status}, "
        f"node_id={(metadata or {}).get('node_id')}, "
        f"content={content[:200]!r}, raw_result={result}"
    )
    logger.error(detail)
    raise RagStoreError(detail)


def batch_store(chunks: list[dict]) -> list[StoreResult]:
    """Store multiple chunks in a single batch operation.

    Args:
        chunks: List of dicts with same keys as store() kwargs.

    Returns:
        One StoreResult per input chunk, index-aligned with `chunks`:
        STORED (with .id), SKIPPED_EMPTY, SKIPPED_INVALID_TYPE, or FAILED
        (with .error). Never raises on a per-batch insert failure — bulk
        callers need per-item outcomes — but every failure is logged.
    """
    if not chunks:
        return []

    results: list[Optional[StoreResult]] = [None] * len(chunks)
    records: list[dict] = []
    record_index: list[int] = []

    for idx, chunk_data in enumerate(chunks):
        content = chunk_data.get("content", "")
        if not content or not content.strip():
            results[idx] = StoreResult(StoreOutcome.SKIPPED_EMPTY)
            continue

        source_type = chunk_data.get("source_type", "ceo_doc")
        if source_type not in VALID_SOURCE_TYPES:
            logger.warning("[RAG] Skipping invalid source_type: %s", source_type)
            results[idx] = StoreResult(StoreOutcome.SKIPPED_INVALID_TYPE)
            continue

        embedding = embed(content, input_type="search_document")
        c_hash = content_hash(content)

        record = {
            "content": content,
            "embedding": embedding,
            "source_type": source_type,
            "section": chunk_data.get("section"),
            "epistemic_status": chunk_data.get("epistemic_status"),
            "topic_tags": chunk_data.get("topic_tags", []),
            "session_id": chunk_data.get("session_id"),
            "run_id": chunk_data.get("run_id"),
            "agent_name": chunk_data.get("agent_name"),
            "confidence": chunk_data.get("confidence"),
            "freshness_policy": chunk_data.get("freshness_policy"),
            "metadata": {
                **(chunk_data.get("metadata") or {}),
                "content_hash": c_hash,
            },
        }
        records.append(record)
        record_index.append(idx)

    if not records:
        return [r for r in results if r is not None]

    supabase = _get_supabase()

    # Insert in chunks of 50 to avoid Supabase statement timeout on large batches
    BATCH_SIZE = 50
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        batch_idx = record_index[i:i + BATCH_SIZE]
        try:
            result = supabase.table(TABLE_NAME).insert(batch).execute()
            rows = result.data or []
            if len(rows) == len(batch):
                for idx, row in zip(batch_idx, rows):
                    results[idx] = StoreResult(StoreOutcome.STORED, id=row["id"])
            else:
                # A short result set means we cannot tell which records landed,
                # so the whole batch is reported as failed rather than guessed at.
                error = f"insert returned {len(rows)} row(s) for {len(batch)} record(s)"
                logger.error(
                    "[RAG] Batch insert incomplete for chunk %d-%d: %s",
                    i,
                    i + len(batch),
                    error,
                )
                for idx in batch_idx:
                    results[idx] = StoreResult(StoreOutcome.FAILED, error=error)
        except Exception as e:  # noqa: BLE001 — per-item reporting, logged below
            logger.error(
                "[RAG] Batch insert failed for chunk %d-%d: %s", i, i + len(batch), e
            )
            for idx in batch_idx:
                results[idx] = StoreResult(StoreOutcome.FAILED, error=str(e))

    final = [r for r in results if r is not None]
    stored = sum(1 for r in final if r)
    failed = sum(1 for r in final if r.outcome is StoreOutcome.FAILED)
    logger.info("[RAG] Batch stored %d chunk(s), %d failed", stored, failed)
    return final


def retrieve(
    query: str,
    source_types: Optional[list[str]] = None,
    section: Optional[str] = None,
    epistemic_status: Optional[list[str]] = None,
    exclude_superseded: bool = True,
    top_k: int = DEFAULT_TOP_K,
    threshold: float = DEFAULT_THRESHOLD,
    recency_boost: bool = True,
    metadata_filter: Optional[dict[str, str]] = None,
    exclude_architecture: bool = True,
) -> list[Chunk]:
    """Retrieve relevant chunks via semantic similarity search.

    Args:
        query: The search query text.
        source_types: Filter to these source types only.
        section: Filter to this business plan section.
        epistemic_status: Filter to these statuses.
        exclude_superseded: If True, skip chunks that have been superseded.
        top_k: Maximum number of results to return.
        threshold: Minimum similarity score (0.0-1.0).
        recency_boost: If True, boost recent results in final ranking.
        metadata_filter: Filter on metadata JSONB keys (all must match).

    Returns:
        List of Chunk objects sorted by relevance.
    """
    query_embedding = embed(query)

    supabase = _get_supabase()

    # Only match_threshold is applied by the RPC. source_types, section,
    # epistemic_status, metadata_filter and exclude_superseded are all applied in
    # Python below, on whatever match_count rows came back — so a narrow window can
    # crowd out the rows the caller actually asked for and return nothing even when
    # a match exists. Widen the window whenever any post-filter is active.
    has_post_filter = bool(
        source_types or section or epistemic_status or metadata_filter
    )
    fetch_multiplier = 10 if has_post_filter else 3
    rpc_params = {
        "query_embedding": query_embedding,
        "match_threshold": threshold,
        "match_count": top_k * fetch_multiplier,
    }

    result = supabase.rpc("match_knowledge_base", rpc_params).execute()

    if not result.data:
        logger.debug("[RAG] No results for query: %s", query[:80])
        return []

    chunks = []
    for row in result.data:
        if exclude_superseded and row.get("superseded_by") is not None:
            continue

        if source_types and row.get("source_type") not in source_types:
            continue

        # Exclude architecture embedding-index rows from fact/evidence retrieval.
        # They share source_type='ceo_doc' with real facts but are the retrieval
        # index (metadata.layer='bp_architecture'/'bp_architecture_aug'), NOT CEO
        # data — so a semantic query must not return node-definition text as a
        # fact. Kept only when the caller explicitly filters for a layer
        # (e.g. match_bp_node), which is exactly when index rows ARE wanted.
        if exclude_architecture and not (metadata_filter and metadata_filter.get("layer")):
            _layer = (row.get("metadata") or {}).get("layer")
            if isinstance(_layer, str) and _layer.startswith("bp_architecture"):
                continue

        if section and row.get("section") != section:
            continue

        if epistemic_status and row.get("epistemic_status") not in epistemic_status:
            continue

        if metadata_filter:
            row_meta = row.get("metadata") or {}
            skip = False
            for key, value in metadata_filter.items():
                if row_meta.get(key) != value:
                    skip = True
                    break
            if skip:
                continue

        chunk = Chunk(
            id=row["id"],
            content=row["content"],
            source_type=row["source_type"],
            similarity=row.get("similarity", 0.0),
            section=row.get("section"),
            epistemic_status=row.get("epistemic_status"),
            topic_tags=row.get("topic_tags", []),
            session_id=row.get("session_id"),
            run_id=row.get("run_id"),
            agent_name=row.get("agent_name"),
            confidence=row.get("confidence"),
            metadata=row.get("metadata", {}),
            created_at=row.get("created_at"),
        )
        chunks.append(chunk)

    if recency_boost and chunks:
        now = datetime.now(timezone.utc)
        for chunk in chunks:
            if chunk.created_at:
                try:
                    created = datetime.fromisoformat(
                        chunk.created_at.replace("Z", "+00:00")
                    )
                    age_days = (now - created).days
                    recency_factor = max(0.8, 1.0 - (age_days / 365.0) * 0.2)
                    chunk.similarity *= recency_factor
                except (ValueError, TypeError):
                    pass

        chunks.sort(key=lambda c: c.similarity, reverse=True)

    return chunks[:top_k]


def supersede(old_chunk_id: str, new_chunk_id: str) -> bool:
    """Mark an old chunk as superseded by a new one.

    Args:
        old_chunk_id: UUID of the chunk being replaced.
        new_chunk_id: UUID of the replacement chunk.

    Returns:
        True if successful.
    """
    supabase = _get_supabase()
    result = (
        supabase.table(TABLE_NAME)
        .update({
            "superseded_by": new_chunk_id,
            "epistemic_status": "SUPERSEDED",
        })
        .eq("id", old_chunk_id)
        .execute()
    )

    if result.data:
        logger.info(
            "[RAG] Superseded chunk %s → %s", old_chunk_id, new_chunk_id
        )
        return True

    logger.error("[RAG] Failed to supersede chunk %s", old_chunk_id)
    return False


def delete(chunk_id: str) -> bool:
    """Delete a chunk from the knowledge base.

    Args:
        chunk_id: UUID of the chunk to delete.

    Returns:
        True if successful.
    """
    supabase = _get_supabase()
    result = (
        supabase.table(TABLE_NAME)
        .delete()
        .eq("id", chunk_id)
        .execute()
    )

    if result.data:
        logger.info("[RAG] Deleted chunk: %s", chunk_id)
        return True

    logger.warning("[RAG] Delete returned no data for chunk: %s", chunk_id)
    return False


def update_metadata(chunk_id: str, new_fields: dict) -> bool:
    """Merge new fields into an existing chunk's metadata JSONB.

    Args:
        chunk_id: UUID of the chunk to update.
        new_fields: Dict of fields to merge (existing keys are overwritten).

    Returns:
        True if successful.
    """
    supabase = _get_supabase()
    try:
        existing = (
            supabase.table(TABLE_NAME)
            .select("metadata")
            .eq("id", chunk_id)
            .limit(1)
            .execute()
        )
        if not existing.data:
            logger.warning("[RAG] update_metadata: chunk %s not found", chunk_id)
            return False

        current_meta = existing.data[0].get("metadata") or {}
        current_meta.update(new_fields)

        result = (
            supabase.table(TABLE_NAME)
            .update({"metadata": current_meta})
            .eq("id", chunk_id)
            .execute()
        )
        if result.data:
            logger.debug("[RAG] Updated metadata for chunk %s: +%d fields", chunk_id, len(new_fields))
            return True

        logger.warning("[RAG] update_metadata returned no data for chunk %s", chunk_id)
        return False
    except Exception as e:
        logger.error("[RAG] update_metadata failed for chunk %s: %s", chunk_id, e)
        return False


def retrieve_with_relationships(
    query: str,
    top_k: int = 5,
    threshold: float = DEFAULT_THRESHOLD,
    source_types: Optional[list[str]] = None,
) -> list[dict]:
    """Retrieve chunks with their relationships to other chunks.

    Args:
        query: The search query text.
        top_k: Maximum number of primary results.
        threshold: Minimum similarity score.
        source_types: Filter to these source types only.

    Returns:
        List of dicts, each with: chunk (Chunk), relationships (list of
        {related_chunk: Chunk, relationship_type: str, confidence: float}).
    """
    from services.memory_index import get_relationships

    chunks = retrieve(
        query=query,
        top_k=top_k,
        threshold=threshold,
        source_types=source_types,
    )

    if not chunks:
        return []

    supabase = _get_supabase()
    results = []

    for chunk in chunks:
        rels = get_relationships(chunk.id)

        enriched_rels = []
        for rel in rels:
            related_id = rel["related_chunk_id"]
            try:
                row_result = (
                    supabase.table(TABLE_NAME)
                    .select("*")
                    .eq("id", related_id)
                    .limit(1)
                    .execute()
                )
                if not row_result.data:
                    continue
                row = row_result.data[0]
                related_chunk = Chunk(
                    id=row["id"],
                    content=row["content"],
                    source_type=row["source_type"],
                    similarity=0.0,
                    section=row.get("section"),
                    epistemic_status=row.get("epistemic_status"),
                    topic_tags=row.get("topic_tags", []),
                    metadata=row.get("metadata", {}),
                    created_at=row.get("created_at"),
                )
                enriched_rels.append({
                    "related_chunk": related_chunk,
                    "relationship_type": rel["relationship_type"],
                    "confidence": rel["confidence"],
                })
            except Exception:
                continue

        results.append({
            "chunk": chunk,
            "relationships": enriched_rels,
        })

    return results


def format_chunks_for_injection(
    chunks: list[Chunk], max_chars: int = 3000
) -> str:
    """Format retrieved chunks into a string suitable for agent prompt injection.

    Args:
        chunks: List of Chunk objects from retrieve().
        max_chars: Maximum total characters for the formatted output.

    Returns:
        Formatted string with source tags and epistemic status.
    """
    if not chunks:
        return ""

    lines = []
    total_chars = 0

    for chunk in chunks:
        status_tag = f"[{chunk.epistemic_status}]" if chunk.epistemic_status else ""
        source_tag = f"({chunk.source_type})"
        line = f"• {status_tag} {chunk.content} {source_tag}"

        if total_chars + len(line) > max_chars:
            break

        lines.append(line)
        total_chars += len(line)

    return "\n".join(lines)
