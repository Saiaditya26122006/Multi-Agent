"""Does context-rich, section-constrained leaf resolution beat isolated
ingestion-time classification? (Go/no-go for the deferred-resolution design.)

The deferred design gives leaf resolution two advantages the ingestion-time call
lacks:
  1. Candidates are the CORRECT section's children (provisional placement already
     got the section right ~77%), not a noisy 747-way pool.
  2. Sibling facts in the same section provide context.

This measures both, on the 40-fact gold set:
  baseline : the full ingestion classifier (classify_and_match_node)  [~35%]
  A        : pick the leaf among the TRUE section's children, NO siblings
  B        : same candidates, WITH sibling gold facts as context

If A alone jumps, the correct-section constraint is the win. If B >> A, siblings
add more. If neither moves, context isn't the lever — don't build it.

Run: python -m evaluation.context_experiment   (live, ~80 LLM calls)
"""

import json
import logging
from collections import defaultdict
from pathlib import Path

logging.disable(logging.WARNING)
_GOLD = Path(__file__).parent / "gold_standard.json"


def _section(nid):
    p = nid.split(".")
    return ".".join(p[:3]) if len(p) >= 3 else nid


def _candidates_for_section(section_id, all_nodes):
    """The section node itself + its direct children, as classify_fact_to_node dicts."""
    out = []
    depth = section_id.count(".")
    for n in all_nodes:
        nid = n.get("node_id", "")
        is_self = nid == section_id
        is_child = nid.startswith(section_id + ".") and nid.count(".") == depth + 1
        if not (is_self or is_child):
            continue
        out.append({
            "node_id": nid,
            "node_title": n.get("node_title") or "",
            "similarity": 0.0,
            "level": n.get("level", 0),
            "purpose": (n.get("purpose") or "")[:250],
            "required_output": (n.get("required_output") or "")[:150],
            "prohibited_claims": (n.get("prohibited_claims_inference_patterns") or "")[:150],
            "parent_node": n.get("parent_node") or "",
        })
    return out


def main():
    from web.handlers.feed_handler import classify_and_match_node, _load_bp_architecture
    from web.handlers.llm_helper import classify_fact_to_node

    gold = json.loads(_GOLD.read_text())
    all_nodes = _load_bp_architecture()

    # sibling facts per section (gold facts sharing a section)
    by_section = defaultdict(list)
    for g in gold:
        by_section[_section(g["proposed_node_id"])].append(g["fact"])

    base_hit = a_hit = b_hit = 0
    a_eligible = 0
    n = len(gold)

    for i, g in enumerate(gold, 1):
        exp = g["proposed_node_id"]
        sec = _section(exp)
        cands = _candidates_for_section(sec, all_nodes)
        if not cands:
            continue
        a_eligible += 1

        # baseline: full ingestion classifier (isolated, real pipeline)
        try:
            base = classify_and_match_node(g["fact"]).get("node_id")
        except Exception:
            base = None
        base_hit += (base == exp)

        # A: correct-section candidates, no sibling context
        try:
            a = classify_fact_to_node(g["fact"], cands, use_fast_model=False).get("node_id")
        except Exception:
            a = None
        a_hit += (a == exp)

        # B: same candidates + sibling gold facts as context
        siblings = [f for f in by_section[sec] if f != g["fact"]]
        ctx = None
        if siblings:
            ctx = "Other facts filed in this same section:\n- " + "\n- ".join(s[:120] for s in siblings[:6])
        try:
            b = classify_fact_to_node(g["fact"], cands, document_context=ctx, use_fast_model=False).get("node_id")
        except Exception:
            b = None
        b_hit += (b == exp)

        print(f"  [{i}/{n}] {exp:12} base={str(base):12} A={str(a):12} B={str(b):12} "
              f"sib={len(siblings)}", flush=True)

    print("\n=== CONTEXT EXPERIMENT (exact-leaf accuracy) ===")
    print(f"  eligible facts (section has candidates): {a_eligible}/{n}")
    print(f"  baseline (isolated ingestion) : {base_hit}/{a_eligible} = {100*base_hit/a_eligible:.0f}%")
    print(f"  A: correct-section, no siblings: {a_hit}/{a_eligible} = {100*a_hit/a_eligible:.0f}%")
    print(f"  B: correct-section + siblings  : {b_hit}/{a_eligible} = {100*b_hit/a_eligible:.0f}%")
    print()
    da = 100 * (a_hit - base_hit) / a_eligible
    db = 100 * (b_hit - a_hit) / a_eligible
    print(f"  section-constraint lift (A-base): {da:+.0f} pts")
    print(f"  sibling-context lift    (B-A)   : {db:+.0f} pts")
    verdict = "BUILD IT" if (a_hit >= base_hit + 4 or b_hit >= base_hit + 4) else "context is NOT the lever"
    print(f"  VERDICT: {verdict}")


if __name__ == "__main__":
    main()
