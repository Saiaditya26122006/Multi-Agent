"""Measure the leaf resolver's GATED precision.

resolve_text is ~52% raw. Before letting it auto-promote facts to leaves, we
must know: when it says "high confidence", how often is it right? That gated
precision is what protects the KB from re-pollution.

Runs resolve_text on each gold fact (at its TRUE section) and reports:
  - coverage : fraction resolved at high confidence (would be auto-promoted)
  - gated precision: of those high-confidence promotions, fraction exact-correct
  - what promotion would cost: precision of the auto-promoted subset

Run: python -m evaluation.measure_resolver   (live, ~40 LLM calls)
"""

import json
import logging
from pathlib import Path

logging.disable(logging.WARNING)
_GOLD = Path(__file__).parent / "gold_standard.json"


def _section(nid):
    p = nid.split(".")
    return ".".join(p[:3]) if len(p) >= 3 else nid


def main():
    from services.leaf_resolver import resolve_text, SUGGEST_MIN_CONFIDENCE

    gold = json.loads(_GOLD.read_text())
    n = 0
    high_total = high_correct = 0
    all_correct = 0

    for g in gold:
        exp = g["proposed_node_id"]
        sec = _section(exp)
        r = resolve_text(g["fact"], sec)
        got = r.get("node_id")
        conf = r.get("confidence")
        n += 1
        correct = (got == exp)
        all_correct += correct
        gated = (conf == SUGGEST_MIN_CONFIDENCE and got and not r.get("none_fit") and got != sec)
        if gated:
            high_total += 1
            high_correct += correct
        print(f"  {exp:12} got={str(got):12} conf={str(conf):6} "
              f"{'PROMOTE' if gated else 'stay   '} {'ok' if correct else 'x'}", flush=True)

    print("\n=== LEAF RESOLVER — gated precision (n={}) ===".format(n))
    print(f"  raw accuracy (all)       : {all_correct}/{n} = {100*all_correct/n:.0f}%")
    print(f"  coverage (auto-promoted) : {high_total}/{n} = {100*high_total/n:.0f}%")
    if high_total:
        print(f"  GATED PRECISION          : {high_correct}/{high_total} = {100*high_correct/high_total:.0f}%")
        print(f"    -> of facts the resolver would promote to a leaf, this % land correctly")
    else:
        print("  GATED PRECISION          : n/a (nothing high-confidence)")
    print()
    if high_total:
        prec = 100 * high_correct / high_total
        if prec >= 85:
            print("  VERDICT: gate is safe to auto-promote (>=85% precision).")
        elif prec >= 70:
            print("  VERDICT: borderline — promote but keep needs_review flag for Alex.")
        else:
            print("  VERDICT: too low to auto-promote — resolve as SUGGESTION only, Alex confirms.")


if __name__ == "__main__":
    main()
