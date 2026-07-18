"""Re-classify the unclassified real facts through the canonical-registry classifier.

The old hardcoded ingestion left ~366 content facts with no BP node. Now that the
classifier runs on Alex's 840-node registry, run each through it and store the
result as PENDING REVIEW — never auto-filed (classifier is ~38% exact / 75%
domain; auto-filing would launder errors). A human promotes proposed_node_id ->
node_id after review.

Idempotent: skips chunks that already have a proposed_node_id. Uses the fast
model. Run: python -m scripts.reclassify_facts
"""

import logging
import re

logging.disable(logging.WARNING)

_BP = re.compile(r"^BP\.\d")


def _is_unclassified(meta: dict) -> bool:
    if meta.get("layer") == "bp_architecture":
        return False
    if meta.get("proposed_node_id") or meta.get("classification_status"):
        return False  # already processed
    nid = meta.get("node_id") or meta.get("primary_node_id")
    return not (nid and _BP.match(str(nid)))


def main() -> None:
    from memory.supabase_client import supabase
    from web.handlers.feed_handler import classify_and_match_node

    rows, start = [], 0
    while True:
        b = supabase.table("knowledge_base").select("id,content,metadata").range(start, start + 999).execute().data
        rows += b
        if len(b) < 1000:
            break
        start += 1000

    todo = [r for r in rows if _is_unclassified(r.get("metadata") or {})]
    print(f"{len(todo)} unclassified facts to process", flush=True)

    done = 0
    for r in todo:
        content = r.get("content") or ""
        if not content.strip():
            continue
        try:
            res = classify_and_match_node(content, use_fast_model=True)
            node_id = res.get("node_id")
            meta = dict(r.get("metadata") or {})
            meta["proposed_node_id"] = node_id
            meta["proposed_confidence"] = res.get("confidence")
            meta["classification_status"] = "pending_review" if node_id else "no_fit"
            supabase.table("knowledge_base").update({"metadata": meta}).eq("id", r["id"]).execute()
        except Exception as e:  # noqa: BLE001
            logging.disable(logging.NOTSET)
            logging.getLogger(__name__).error("classify failed for %s: %s", r["id"], e)
            logging.disable(logging.WARNING)
            continue
        done += 1
        if done % 25 == 0:
            print(f"  processed {done}/{len(todo)}", flush=True)

    # summary
    from collections import Counter

    rows2, start = [], 0
    while True:
        b = supabase.table("knowledge_base").select("metadata").range(start, start + 999).execute().data
        rows2 += b
        if len(b) < 1000:
            break
        start += 1000
    statuses = Counter((r.get("metadata") or {}).get("classification_status") for r in rows2)
    print(f"\nDONE. processed {done}. classification_status spread: {dict(statuses)}", flush=True)


if __name__ == "__main__":
    main()
