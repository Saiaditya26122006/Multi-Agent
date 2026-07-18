"""Populate BP.12 from existing data (Alex audit concern 4 / issue #7).

Contradiction detection fires on new ingestion, but existing facts were never
scanned, so bp12_register is empty. This scans facts that share a BP node,
LLM-judges each pair for incompatibility, and files genuine contradictions to
BP.12 for controller review (never auto-resolved). Also files evidence_gap items
for evidence links marked insufficient.

Idempotent: skips chunk pairs already filed. Run: python -m scripts.populate_bp12
"""

import logging
import re
from collections import defaultdict
from itertools import combinations

logging.disable(logging.WARNING)

_BP = re.compile(r"^BP\.\d")


def main() -> None:
    from memory.supabase_client import supabase
    from web.handlers.llm_helper import judge_contradiction
    from services.bp12_register import create_register_item

    # existing contradiction items -> the chunk pairs already filed (dedup)
    existing = supabase.table("bp12_register").select("affected_chunk_ids,item_type").execute().data or []
    filed_pairs = set()
    for it in existing:
        ids = it.get("affected_chunk_ids") or []
        if len(ids) >= 2:
            filed_pairs.add(frozenset(ids[:2]))

    # group content facts by node (filed node_id or proposed_node_id)
    rows, start = [], 0
    while True:
        b = supabase.table("knowledge_base").select("id,content,metadata,epistemic_status").range(start, start + 999).execute().data
        rows += b
        if len(b) < 1000:
            break
        start += 1000
    groups = defaultdict(list)
    for r in rows:
        m = r.get("metadata") or {}
        if m.get("layer") == "bp_architecture":
            continue
        nid = m.get("node_id") or m.get("proposed_node_id")
        if nid and _BP.match(str(nid)):
            groups[nid].append(r)

    multi = {k: v for k, v in groups.items() if len(v) >= 2}
    print(f"{len(multi)} nodes with 2+ facts; judging pairs...", flush=True)

    contradictions = 0
    judged = 0
    for nid, facts in multi.items():
        for a, b in combinations(facts, 2):
            if frozenset([a["id"], b["id"]]) in filed_pairs:
                continue
            judged += 1
            verdict = judge_contradiction(a.get("content") or "", b.get("content") or "")
            if verdict.get("contradicts"):
                create_register_item(
                    item_type="contradiction",
                    title=f"Contradiction in {nid}: '{(a.get('content') or '')[:40]}' vs '{(b.get('content') or '')[:40]}'",
                    description=(
                        f"Two facts on {nid} make incompatible claims. Reason: "
                        f"{verdict.get('reason') or 'incompatible'}. Controller decides: "
                        f"keep one, reclassify, or accept both."
                    ),
                    affected_chunk_ids=[a["id"], b["id"]],
                    affected_node_ids=[nid],
                    severity="medium",
                )
                filed_pairs.add(frozenset([a["id"], b["id"]]))
                contradictions += 1
            if judged % 50 == 0:
                print(f"  judged {judged} pairs, {contradictions} contradictions", flush=True)

    # evidence gaps: insufficient evidence links -> BP.12 evidence_gap
    gaps = 0
    links = supabase.table("evidence_links").select("*").eq("sufficiency_status", "insufficient").execute().data or []
    existing_gaps = {
        (it.get("affected_node_ids") or [None])[0]
        for it in existing if it.get("item_type") == "evidence_gap"
    }
    for link in links:
        node = link.get("target_node_id")
        if node in existing_gaps:
            continue
        create_register_item(
            item_type="evidence_gap",
            title=f"Evidence gap in {node}: claim not sufficiently supported",
            description=(
                f"Claim '{str(link.get('candidate_claim'))[:80]}' on {node} has "
                f"insufficient evidence. Controller decides: gather evidence, "
                f"downgrade the claim, or accept the gap."
            ),
            affected_chunk_ids=[link["chunk_id"]] if link.get("chunk_id") else [],
            affected_node_ids=[node] if node else [],
            severity="medium",
        )
        existing_gaps.add(node)
        gaps += 1

    total = supabase.table("bp12_register").select("id", count="exact").execute().count
    print(f"\nDONE. judged {judged} pairs -> {contradictions} contradictions, {gaps} evidence gaps filed.", flush=True)
    print(f"bp12_register now holds {total} item(s).", flush=True)


if __name__ == "__main__":
    main()
