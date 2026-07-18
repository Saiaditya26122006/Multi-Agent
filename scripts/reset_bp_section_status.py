"""
One-off reset of business_plan_sections.status.

A bulk load stamped all 746 BP nodes with status='approved' — the final state
of the governance chain — without any node having been through it. That makes
get_open_business_plan_sections() return nothing, so L1 and the router tell the
CEO the whole plan is complete.

This resets every node to its earned status:
  - Nodes that have real evidence attached (an evidence_link, a filed knowledge
    chunk, or a decision referencing them) -> 'in_progress'
  - Every other node -> 'not_started'

'approved' is never written here. It is now reachable only through
set_section_status(..., controller_approved=True) — a controller decision.

Idempotent: safe to re-run. Run with `python -m scripts.reset_bp_section_status`.
"""

import logging
import re
from collections import Counter

from memory.supabase_client import supabase

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_BP_NODE_RE = re.compile(r"^BP\.\d")


def _nodes_with_evidence() -> set[str]:
    """Return the set of BP node IDs that have any evidence or activity attached.

    A node is 'in_progress' if at least one of these points at it:
      - a row in evidence_links (target_node_id)
      - a knowledge_base chunk filed to it (metadata.node_id / primary_node_id)
      - a decision referencing it (decisions.sections_affected)
    """
    active: set[str] = set()

    for row in supabase.table("evidence_links").select("target_node_id").execute().data:
        node_id = row.get("target_node_id")
        if node_id:
            active.add(node_id)

    for row in supabase.table("decisions").select("sections_affected").execute().data:
        for node_id in row.get("sections_affected") or []:
            active.add(node_id)

    start = 0
    while True:
        batch = (
            supabase.table("knowledge_base")
            .select("metadata")
            .range(start, start + 999)
            .execute()
            .data
        )
        for row in batch:
            meta = row.get("metadata") or {}
            if meta.get("layer") == "bp_architecture":
                continue
            node_id = meta.get("node_id") or meta.get("primary_node_id")
            if node_id and _BP_NODE_RE.match(str(node_id)):
                active.add(node_id)
        if len(batch) < 1000:
            break
        start += 1000

    return active


def reset_section_status() -> None:
    """Reset every business_plan_sections row to its earned status."""
    registry_ids = {
        row["section_id"]
        for row in supabase.table("business_plan_sections")
        .select("section_id")
        .execute()
        .data
    }
    logger.info("business_plan_sections registry holds %d nodes", len(registry_ids))

    evidence_nodes = _nodes_with_evidence()
    in_registry = sorted(evidence_nodes & registry_ids)
    orphaned = sorted(evidence_nodes - registry_ids)

    logger.info("%d nodes have evidence attached: %s", len(in_registry), in_registry)
    if orphaned:
        logger.warning(
            "%d nodes have evidence but NO registry row (inconsistent node IDs — "
            "evidence is filed to a node that does not exist in business_plan_sections): %s",
            len(orphaned),
            orphaned,
        )

    # Step 1: everything not already there -> not_started.
    supabase.table("business_plan_sections").update({"status": "not_started"}).neq(
        "status", "not_started"
    ).execute()

    # Step 2: evidence-bearing nodes -> in_progress.
    if in_registry:
        supabase.table("business_plan_sections").update({"status": "in_progress"}).in_(
            "section_id", in_registry
        ).execute()

    final = Counter(
        row["status"]
        for row in supabase.table("business_plan_sections")
        .select("status")
        .execute()
        .data
    )
    logger.info("Done. Status distribution now: %s", dict(final))
    if final.get("approved"):
        logger.error(
            "%d nodes are still 'approved' — investigate before trusting the reset",
            final["approved"],
        )


if __name__ == "__main__":
    reset_section_status()
