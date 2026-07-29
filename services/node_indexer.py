"""
Node Indexer — embeds and indexes SSoT nodes for retrieval.

One-time ingestion of the BP architecture nodes into the RAG store.
Each node gets an augmented embedding combining ID, section, purpose,
key terms, and prohibited claims for maximum disambiguation.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BP_ARCHITECTURE_PATH = Path(__file__).parent.parent / "ceo_data" / "bp_architecture.json"
SSOT_NODES_PATH = Path(__file__).parent.parent / "ceo_data" / "ssot_nodes.json"

SOURCE_TYPE = "ssot_node"


def _load_architecture() -> list[dict]:
    """Load the BP architecture nodes."""
    with open(BP_ARCHITECTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("nodes", [])


def build_augmented_text(node: dict) -> str:
    """Build augmented embedding text for a node.

    Combines multiple fields for richer semantic surface area:
    "{id} | {section_title} | {purpose/title} | {required_output} | NOT: {prohibited}"

    Args:
        node: A node dict from bp_architecture.json.

    Returns:
        Augmented text string for embedding.
    """
    node_id = node.get("node_id", "")
    title = node.get("node_title", "")
    purpose = node.get("purpose", title)
    required_output = node.get("required_output", "")
    prohibited = node.get("prohibited_claims", "")

    parts = [node_id]

    if title:
        parts.append(title)
    if purpose and purpose != title:
        parts.append(purpose)
    if required_output:
        parts.append(f"requires: {required_output[:100]}")
    if prohibited:
        parts.append(f"NOT: {prohibited[:80]}")

    return " | ".join(parts)


def get_section_for_node(node_id: str) -> Optional[str]:
    """Extract the top-level section from a node ID.

    Args:
        node_id: e.g. "BP.1.1.3"

    Returns:
        Section string, e.g. "1".
    """
    parts = node_id.split(".")
    if len(parts) >= 2:
        return parts[1]
    return None


def get_all_sections() -> list[dict]:
    """Get unique top-level sections with their titles.

    Returns:
        List of dicts with section_id, section_num, title.
    """
    nodes = _load_architecture()
    sections = {}

    for node in nodes:
        node_id = node.get("node_id", "")
        parts = node_id.split(".")
        if len(parts) >= 2:
            section_num = parts[1]
            section_key = f"BP.{section_num}"
            if section_key not in sections:
                if node.get("level") in (1, 2) or node_id == section_key:
                    sections[section_key] = {
                        "section_id": section_key,
                        "section_num": section_num,
                        "title": node.get("node_title", ""),
                    }

    return list(sections.values())


def get_section_descriptions() -> dict[str, str]:
    """Get a description for each section for the hierarchical classifier.

    Returns:
        Dict mapping section_id to a description string.
    """
    sections = get_all_sections()
    descriptions = {}
    for s in sections:
        descriptions[s["section_id"]] = s.get("title", s["section_id"])
    return descriptions


def ingest_nodes() -> dict:
    """Ingest all architecture nodes into the RAG store as ssot_node type.

    Embeds each node with augmented text and stores with metadata.

    Returns:
        Dict with: total_nodes, ingested_count, skipped_count.
    """
    from services.rag_service import store, VALID_SOURCE_TYPES

    if SOURCE_TYPE not in VALID_SOURCE_TYPES:
        logger.error(
            "[NodeIndexer] '%s' not in VALID_SOURCE_TYPES. Add it to rag_service.py",
            SOURCE_TYPE,
        )
        return {"total_nodes": 0, "ingested_count": 0, "skipped_count": 0, "error": "invalid source_type"}

    nodes = _load_architecture()
    ingested = 0
    skipped = 0

    for node in nodes:
        node_id = node.get("node_id", "")
        augmented_text = build_augmented_text(node)
        section = get_section_for_node(node_id)

        result = store(
            content=augmented_text,
            source_type=SOURCE_TYPE,
            section=section,
            topic_tags=["ssot-node", node_id, node.get("node_type", "")],
            metadata={
                "node_id": node_id,
                "node_title": node.get("node_title", ""),
                "purpose": node.get("purpose", ""),
                "required_output": node.get("required_output", ""),
                "prohibited_claims": node.get("prohibited_claims", ""),
                "proof_burden": node.get("proof_burden", ""),
                "level": node.get("level"),
                "parent_node": node.get("parent_node"),
                "node_type": node.get("node_type", ""),
            },
            deduplicate=True,
        )

        # A falsy result is now always a dedup skip — write failures raise.
        if result:
            ingested += 1
        else:
            skipped += 1

    logger.info(
        "[NodeIndexer] Ingested %d/%d nodes (%d skipped/deduped)",
        ingested,
        len(nodes),
        skipped,
    )

    return {
        "total_nodes": len(nodes),
        "ingested_count": ingested,
        "skipped_count": skipped,
    }


def retrieve_candidate_nodes(
    fact: str,
    section: Optional[str] = None,
    top_k: int = 5,
    threshold: float = 0.35,
) -> list[dict]:
    """Retrieve candidate SSoT nodes for a fact.

    Args:
        fact: The fact to find a node for.
        section: Optional section filter (e.g., "1").
        top_k: Number of candidates to return.
        threshold: Minimum similarity.

    Returns:
        List of node dicts with node_id, title, similarity, prohibited_claims.
    """
    from services.rag_service import retrieve

    chunks = retrieve(
        query=fact,
        source_types=[SOURCE_TYPE],
        section=section,
        top_k=top_k,
        threshold=threshold,
    )

    candidates = []
    for chunk in chunks:
        candidates.append({
            "node_id": chunk.metadata.get("node_id", ""),
            "node_title": chunk.metadata.get("node_title", ""),
            "purpose": chunk.metadata.get("purpose", ""),
            "required_output": chunk.metadata.get("required_output", ""),
            "prohibited_claims": chunk.metadata.get("prohibited_claims", ""),
            "similarity": chunk.similarity,
            "content": chunk.content,
        })

    return candidates


def verify_retrieval_quality(test_facts: list[dict], top_k: int = 5) -> dict:
    """Test retrieval quality: for known fact→node mappings, check if correct node is in top-k.

    Args:
        test_facts: List of dicts with "fact" and "expected_node_id".
        top_k: How many candidates to check.

    Returns:
        Dict with: total, hits, misses, hit_rate, details.
    """
    hits = 0
    misses = 0
    details = []

    for test in test_facts:
        fact = test["fact"]
        expected = test["expected_node_id"]

        candidates = retrieve_candidate_nodes(fact, top_k=top_k)
        candidate_ids = [c["node_id"] for c in candidates]

        found = expected in candidate_ids
        if found:
            hits += 1
            rank = candidate_ids.index(expected) + 1
        else:
            misses += 1
            rank = None

        details.append({
            "fact": fact[:60],
            "expected": expected,
            "found": found,
            "rank": rank,
            "top_candidates": candidate_ids[:3],
        })

    total = hits + misses
    hit_rate = (hits / total * 100) if total else 0

    return {
        "total": total,
        "hits": hits,
        "misses": misses,
        "hit_rate": round(hit_rate, 1),
        "details": details,
    }


def check_architecture_hash() -> dict:
    """Check if bp_architecture.json has changed since last ingestion."""
    import hashlib
    arch_path = Path(__file__).parent.parent / "ceo_data" / "bp_architecture.json"
    hash_path = Path(__file__).parent.parent / "ceo_data" / ".architecture_hash"

    if not arch_path.exists():
        return {"changed": False, "modified_nodes": []}

    current_hash = hashlib.sha256(arch_path.read_bytes()).hexdigest()

    if not hash_path.exists():
        return {"changed": True, "modified_nodes": [], "current_hash": current_hash}

    stored_hash = hash_path.read_text().strip()
    changed = current_hash != stored_hash
    return {"changed": changed, "current_hash": current_hash, "stored_hash": stored_hash, "modified_nodes": []}


def update_architecture_hash() -> None:
    """Store current hash of bp_architecture.json."""
    import hashlib
    arch_path = Path(__file__).parent.parent / "ceo_data" / "bp_architecture.json"
    hash_path = Path(__file__).parent.parent / "ceo_data" / ".architecture_hash"

    if arch_path.exists():
        current_hash = hashlib.sha256(arch_path.read_bytes()).hexdigest()
        hash_path.write_text(current_hash)
        logger.info("[NodeIndexer] Architecture hash updated: %s", current_hash[:12])
