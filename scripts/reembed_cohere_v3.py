"""
Re-embed all knowledge_base rows with Cohere Embed v3 (1024 dims).

Run AFTER the SQL migration (upgrade_embedding_cohere_v3.sql).
Processes rows in batches to avoid memory/timeout issues.

Usage:
    python -m scripts.reembed_cohere_v3
    python -m scripts.reembed_cohere_v3 --dry-run
    python -m scripts.reembed_cohere_v3 --batch-size 50
"""

import argparse
import logging
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.embedding_service import embed_batch, EMBEDDING_DIM
from supabase import create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

TABLE_NAME = "knowledge_base"
DEFAULT_BATCH_SIZE = 50


def get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")
    return create_client(url, key)


def count_rows(supabase) -> int:
    """Count total rows in knowledge_base."""
    result = supabase.table(TABLE_NAME).select("id", count="exact").execute()
    return result.count or 0


def fetch_batch(supabase, offset: int, limit: int) -> list[dict]:
    """Fetch a batch of rows ordered by created_at."""
    result = (
        supabase.table(TABLE_NAME)
        .select("id, content")
        .order("created_at")
        .range(offset, offset + limit - 1)
        .execute()
    )
    return result.data or []


def update_embedding(supabase, row_id: str, embedding: list[float]) -> bool:
    """Update a single row's embedding."""
    result = (
        supabase.table(TABLE_NAME)
        .update({"embedding": embedding})
        .eq("id", row_id)
        .execute()
    )
    return bool(result.data)


def run(batch_size: int = DEFAULT_BATCH_SIZE, dry_run: bool = False):
    supabase = get_supabase()
    total = count_rows(supabase)

    logger.info(
        "Re-embedding %d rows with Cohere Embed v3 (%d dims)%s",
        total,
        EMBEDDING_DIM,
        " [DRY RUN]" if dry_run else "",
    )

    if total == 0:
        logger.info("No rows to process.")
        return

    processed = 0
    failed = 0
    offset = 0

    start_time = time.time()

    while offset < total:
        batch = fetch_batch(supabase, offset, batch_size)
        if not batch:
            break

        texts = [row["content"] for row in batch]
        ids = [row["id"] for row in batch]

        if dry_run:
            logger.info(
                "[DRY RUN] Would embed batch %d-%d (%d texts)",
                offset,
                offset + len(batch),
                len(texts),
            )
            offset += len(batch)
            processed += len(batch)
            continue

        try:
            embeddings = embed_batch(texts, input_type="search_document")
        except Exception as e:
            logger.error("Embedding batch failed at offset %d: %s", offset, e)
            failed += len(batch)
            offset += len(batch)
            continue

        for row_id, embedding in zip(ids, embeddings):
            try:
                update_embedding(supabase, row_id, embedding)
                processed += 1
            except Exception as e:
                logger.error("Failed to update row %s: %s", row_id, e)
                failed += 1

        elapsed = time.time() - start_time
        rate = processed / elapsed if elapsed > 0 else 0
        logger.info(
            "Progress: %d/%d (%.1f%%) | %.1f rows/sec | %d failed",
            processed,
            total,
            (processed / total) * 100,
            rate,
            failed,
        )

        offset += len(batch)

    elapsed = time.time() - start_time
    logger.info(
        "Done. Processed: %d | Failed: %d | Time: %.1fs",
        processed,
        failed,
        elapsed,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-embed knowledge_base with Cohere v3")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(batch_size=args.batch_size, dry_run=args.dry_run)
