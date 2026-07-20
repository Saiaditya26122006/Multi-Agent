"""Diagnose whether RETRIEVAL or SELECTION is the leaf bottleneck.

For each gold fact, check whether the correct node (and its section) is present
in the candidate pool that match_bp_node() retrieves. This is the ceiling: if
the correct node isn't in the pool, no LLM selection can ever pick it, and the
fix is retrieval (e.g. the augmented index). If it IS in the pool but the final
classifier still misses, the fix is SELECTION (Step A/B prompt), not retrieval.

Embedding-only, no LLM selection calls. Run: python -m evaluation.retrieval_recall
"""

import json
import logging
from pathlib import Path

logging.disable(logging.WARNING)
_GOLD = Path(__file__).parent / "gold_standard.json"


def _section(nid):
    p = nid.split(".") if nid else []
    return ".".join(p[:3]) if len(p) >= 3 else nid


def main():
    from web.handlers.feed_handler import match_bp_node

    gold = json.loads(_GOLD.read_text())
    n = len(gold)
    for K in (15, 30):
        exact_hit = alt_hit = sect_hit = empty = 0
        rank_sum = 0
        ranked = 0
        for g in gold:
            exp = g["proposed_node_id"]
            alts = {a["node_id"] for a in g.get("alternatives", []) if a.get("node_id")}
            acceptable = {exp} | alts
            cands = match_bp_node(g["fact"], top_k=K)
            ids = [c.get("node_id") for c in cands]
            if not ids:
                empty += 1
            exact_hit += exp in ids
            alt_hit += bool(acceptable & set(ids))
            sect_hit += _section(exp) in {_section(i) for i in ids}
            if exp in ids:
                rank_sum += ids.index(exp) + 1
                ranked += 1
        print(f"\n=== match_bp_node pool @ top_k={K} (n={n}) ===")
        print(f"  EXACT proposed node in pool    : {exact_hit}/{n} = {100*exact_hit/n:.0f}%   <- can the LLM even pick it?")
        print(f"  correct SECTION in pool        : {sect_hit}/{n} = {100*sect_hit/n:.0f}%")
        print(f"  exact-or-acceptable-alt in pool: {alt_hit}/{n} = {100*alt_hit/n:.0f}%   (permissive ceiling)")
        print(f"  empty pools (retrieved nothing): {empty}/{n}")
        print(f"  mean rank of exact node when present: "
              + (f"{rank_sum/ranked:.1f} (of {ranked})" if ranked else "n/a"))


if __name__ == "__main__":
    main()
