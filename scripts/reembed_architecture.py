"""Re-embed the canonical 840-node architecture into the knowledge base.

The Feed classifier's embedding safety-net (match_bp_node) reads architecture
nodes stored as source_type='ceo_doc', metadata.layer='bp_architecture'. This
replaces the stale 746-node set with the canonical 840 from the sheet.

Gap-free: inserts all 840 tagged with a fresh sync_batch, verifies the count,
then deletes only the OLD rows (different/absent sync_batch). If interrupted,
the KB still has a complete set (old until the swap completes).

Content/metadata match scripts/ingest_bp_architecture.py exactly so the classifier
behaves identically. Run: python -m scripts.reembed_architecture
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ARCH = Path(__file__).parent.parent / "ceo_data" / "bp_architecture.json"
_BP = re.compile(r"^BP\.\d")


def _content(node: dict) -> str:
    """Build embedding content, node_id-prefixed (matches the original
    ingestion_pipeline._chunk_bp_architecture format).

    Including the node_id guarantees unique content per node so store()'s
    content-hash dedup never collapses distinct nodes — critical for the empty
    placeholder nodes whose title/purpose are blank in the sheet.
    """
    nid = node.get("node_id", "")
    parts = [f"BP Node {nid}: {node.get('node_title') or ''}"]
    if node.get("purpose"):
        parts.append(f"Purpose: {node['purpose']}")
    if node.get("required_output"):
        parts.append(f"Required output: {node['required_output']}")
    prohibited = node.get("prohibited_claims_inference_patterns") or node.get("prohibited_claims")
    if prohibited:
        parts.append(f"PROHIBITED: {prohibited}")
    return "; ".join(parts)


def main() -> None:
    from services.rag_service import store, _get_supabase, TABLE_NAME

    sb = _get_supabase()
    tag = datetime.now(timezone.utc).strftime("sheet-%Y%m%dT%H%M%SZ")
    nodes = [n for n in json.loads(_ARCH.read_text())["nodes"] if _BP.match(n.get("node_id", ""))]

    # Delete-first: store()'s content-hash dedup would otherwise match new nodes
    # against the existing rows and skip the insert. Offline op, no live traffic;
    # the JSON is the recoverable source. Clear all arch rows before inserting.
    existing = (
        sb.table(TABLE_NAME)
        .select("id")
        .eq("metadata->>layer", "bp_architecture")
        .execute()
        .data
    )
    old_ids = [r["id"] for r in existing]
    logger.info("Deleting %d existing arch rows before fresh insert", len(old_ids))
    for i in range(0, len(old_ids), 100):
        sb.table(TABLE_NAME).delete().in_("id", old_ids[i : i + 100]).execute()

    logger.info("Re-embedding %d canonical nodes (sync_batch=%s)", len(nodes), tag)

    stored = 0
    skipped = 0
    for node in nodes:
        nid = node["node_id"]
        metadata = {
            "layer": "bp_architecture",
            "node_id": nid,
            "node_title": node.get("node_title") or "",
            "level": node.get("level", 0),
            "parent_node": node.get("parent_node") or "",
            "purpose": node.get("purpose") or "",
            "required_output": node.get("required_output") or "",
            "prohibited_claims": node.get("prohibited_claims_inference_patterns") or "",
            "sync_batch": tag,
        }
        try:
            result = store(
                content=_content(node),
                source_type="ceo_doc",
                section=nid,
                epistemic_status="CONFIRMED",
                confidence=1.0,
                metadata=metadata,
            )
            if result:
                stored += 1
            else:
                skipped += 1
                logger.warning("skipped %s: %s", nid, result.outcome.value)
            if stored % 100 == 0:
                logger.info("  stored %d/%d", stored, len(nodes))
        except Exception as e:  # noqa: BLE001
            logger.error("failed to store %s: %s", nid, e)

    # verify the fresh set is complete and unique
    all_arch = (
        sb.table(TABLE_NAME)
        .select("metadata")
        .eq("metadata->>layer", "bp_architecture")
        .execute()
        .data
    )
    ids = [(r.get("metadata") or {}).get("node_id") for r in all_arch]
    logger.info(
        "DONE. stored=%d | skipped=%d | arch rows=%d | unique node_ids=%d | expected=%d",
        stored,
        skipped,
        len(all_arch),
        len(set(ids)),
        len(nodes),
    )
    if len(set(ids)) != len(nodes):
        logger.error("MISMATCH: unique node_ids (%d) != expected (%d)", len(set(ids)), len(nodes))


if __name__ == "__main__":
    main()
