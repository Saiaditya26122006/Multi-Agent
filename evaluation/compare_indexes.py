"""Does the AUGMENTED node index retrieve the correct node better than the
plain index? Embedding-only A/B on the gold set — the deciding Phase 1b test.

  plain      : match_bp_node()            -> ceo_doc / layer=bp_architecture
  augmented  : retrieve_candidate_nodes() -> ssot_node (id|title|purpose|
                                             requires|NOT-prohibited)

Reports EXACT-node recall and SECTION recall in the top-k pool for each.
If augmented lifts exact-node recall meaningfully, wire it into the classifier.
Run: python -m evaluation.compare_indexes
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
    # plain = live ceo_doc/bp_architecture index (embeds "BP Node id: title; Purpose: ...")
    # augmented = in-memory embeddings of "title. purpose. required_output"
    #   (services.node_indexer's ssot_node index can't be populated — the DB
    #    source_type check constraint rejects 'ssot_node' — so we test the
    #    richer-text hypothesis in-memory via _direct_node_match instead.)
    from web.handlers.feed_handler import match_bp_node, _direct_node_match

    gold = json.loads(_GOLD.read_text())
    n = len(gold)
    K = 15

    p_exact = p_sect = p_empty = 0
    a_exact = a_sect = a_empty = 0
    flipped = []  # augmented finds exact, plain didn't

    for g in gold:
        exp = g["proposed_node_id"]
        # plain
        p_ids = [c.get("node_id") for c in match_bp_node(g["fact"], top_k=K)]
        # richer in-memory embeddings
        a_ids = [c.get("node_id") for c in _direct_node_match(g["fact"], top_k=K)]

        p_hit = exp in p_ids
        a_hit = exp in a_ids
        p_exact += p_hit
        a_exact += a_hit
        p_sect += _section(exp) in {_section(i) for i in p_ids}
        a_sect += _section(exp) in {_section(i) for i in a_ids}
        p_empty += not p_ids
        a_empty += not a_ids
        if a_hit and not p_hit:
            flipped.append((exp, g["fact"][:44]))

    print(f"=== Index A/B on {n} gold facts, top_k={K} ===\n")
    print(f"{'metric':28} {'plain':>8} {'augmented':>11}")
    print("-" * 50)
    print(f"{'EXACT node in pool':28} {p_exact}/{n} = {100*p_exact/n:>3.0f}% {a_exact}/{n} = {100*a_exact/n:>3.0f}%")
    print(f"{'SECTION in pool':28} {p_sect}/{n} = {100*p_sect/n:>3.0f}% {a_sect}/{n} = {100*a_sect/n:>3.0f}%")
    print(f"{'empty pools':28} {p_empty:>8} {a_empty:>11}")
    print()
    if flipped:
        print(f"Augmented found the exact node where plain missed ({len(flipped)}):")
        for exp, fact in flipped:
            print(f"  {exp:12} | {fact}")
    else:
        print("Augmented found no exact node that plain missed.")
    delta = 100 * (a_exact - p_exact) / n
    print(f"\nEXACT-node recall delta: {delta:+.0f} pts "
          f"({'augmented WINS -> wire it in' if delta >= 8 else 'not worth switching -> retrieval richness is not the lever'})")


if __name__ == "__main__":
    main()
