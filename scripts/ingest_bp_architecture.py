#!/usr/bin/env python3
"""Ingest bp_architecture.json into RAG for classifier matching.

The Feed classifier depends on RAG containing embedded architecture nodes
with metadata.layer="bp_architecture". This script loads bp_architecture.json
and stores each node with proper metadata so match_bp_node() can find them.
"""

import json
import logging
from pathlib import Path

from services.rag_service import store, _get_supabase, TABLE_NAME

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def ingest_bp_architecture():
    """Load bp_architecture.json and store all nodes in RAG."""

    # Load architecture
    arch_path = Path(__file__).parent.parent / "ceo_data" / "bp_architecture.json"
    if not arch_path.exists():
        logger.error(f"bp_architecture.json not found at {arch_path}")
        return

    with open(arch_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes = data.get("nodes", [])
    logger.info(f"Loaded {len(nodes)} nodes from bp_architecture.json")

    # Check if already ingested
    supabase = _get_supabase()
    existing = supabase.table(TABLE_NAME).select("id", count="exact").eq("source_type", "ceo_doc").contains("metadata", {"layer": "bp_architecture"}).limit(1).execute()

    if existing.count > 0:
        logger.warning(f"Found {existing.count} existing bp_architecture rows in RAG. Delete them first? (y/n)")
        response = input().strip().lower()
        if response == 'y':
            # Delete existing
            logger.info("Deleting existing bp_architecture rows...")
            supabase.table(TABLE_NAME).delete().eq("source_type", "ceo_doc").contains("metadata", {"layer": "bp_architecture"}).execute()
            logger.info("Deleted.")

    # Store each node
    logger.info("Storing nodes in RAG...")
    stored_count = 0

    for node in nodes:
        node_id = node.get("node_id")
        if not node_id or not node_id.startswith("BP."):
            continue

        # Build content for embedding
        node_title = node.get("node_title") or ""
        purpose = node.get("purpose") or ""
        required_output = node.get("required_output") or ""
        prohibited = node.get("prohibited_claims_inference_patterns") or ""

        content = f"{node_title}. {purpose}. Required output: {required_output}"
        if prohibited:
            content += f". Prohibited claims: {prohibited}"

        # Metadata
        metadata = {
            "layer": "bp_architecture",
            "node_id": node_id,
            "node_title": node_title,
            "level": node.get("level", 0),
            "parent_node": node.get("parent_node", ""),
            "purpose": purpose,
            "required_output": required_output,
            "prohibited_claims": prohibited,
        }

        # Store
        try:
            result = store(
                content=content,
                source_type="ceo_doc",
                section=node_id,
                epistemic_status="CONFIRMED",
                confidence=1.0,
                metadata=metadata,
            )
            if result:
                stored_count += 1
            else:
                logger.warning(f"Skipped {node_id}: {result.outcome.value}")
            if stored_count % 50 == 0:
                logger.info(f"  Stored {stored_count}/{len(nodes)}...")
        except Exception as e:
            logger.error(f"Failed to store {node_id}: {e}")

    logger.info(f"✅ Ingestion complete: {stored_count}/{len(nodes)} nodes stored in RAG")
    logger.info("The classifier can now find architecture nodes via match_bp_node()")


if __name__ == "__main__":
    ingest_bp_architecture()
