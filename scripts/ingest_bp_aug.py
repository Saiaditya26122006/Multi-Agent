"""Build a DB-backed *augmented* BP-node index for fast retrieval.

Measured (evaluation/compare_indexes.py): embedding richer node text
("title. purpose. required_output") instead of the plain ceo_doc text lifts
exact-node retrieval 45%->68% and section retrieval 50%->75% on the gold set.

The services.node_indexer 'ssot_node' source_type is rejected by the DB
knowledge_base_source_type_check constraint, so we store under the allowed
'ceo_doc' source_type with metadata.layer='bp_architecture_aug' — a parallel
layer match_bp_node can query, leaving the existing 'bp_architecture' layer
untouched (safe rollback: just delete this layer).

Run once: python -m scripts.ingest_bp_aug
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AUG_LAYER = "bp_architecture_aug"


def _aug_text(node: dict) -> str:
    """Same richer text that tested at 68% exact / 75% section recall."""
    title = node.get("node_title") or ""
    purpose = node.get("purpose") or ""
    required = node.get("required_output") or ""
    return f"{title}. {purpose}. {required}".strip()


def main() -> None:
    from services.rag_service import store, _get_supabase

    arch = json.loads(
        (Path(__file__).parent.parent / "ceo_data" / "bp_architecture.json").read_text()
    )
    nodes = [n for n in arch.get("nodes", []) if str(n.get("node_id", "")).startswith("BP.")]

    # Idempotent: clear any prior aug layer first.
    sb = _get_supabase()
    existing = sb.table("knowledge_base").select("id").contains(
        "metadata", {"layer": AUG_LAYER}
    ).execute()
    if existing.data:
        for row in existing.data:
            sb.table("knowledge_base").delete().eq("id", row["id"]).execute()
        logger.info("Cleared %d existing aug rows", len(existing.data))

    ingested = 0
    for node in nodes:
        nid = node.get("node_id")
        text = _aug_text(node)
        if not text or text == "..":
            continue
        chunk_id = store(
            content=text,
            source_type="ceo_doc",
            section=nid.split(".")[1] if "." in nid else None,
            topic_tags=["bp-node-aug", nid],
            metadata={
                "layer": AUG_LAYER,
                "node_id": nid,
                "node_title": node.get("node_title", ""),
                "level": node.get("level"),
            },
            deduplicate=False,
        )
        if chunk_id:
            ingested += 1
        if ingested % 100 == 0:
            logger.info("  ingested %d/%d", ingested, len(nodes))

    logger.info("Done: ingested %d aug nodes under layer=%s", ingested, AUG_LAYER)


if __name__ == "__main__":
    main()
