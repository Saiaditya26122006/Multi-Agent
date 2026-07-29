#!/usr/bin/env python3
"""Measure Feed classifier v1 against the answer key.

Runs every fact in evaluation/classifier_answer_key_draft.csv through the
hierarchical embedding classifier and reports the six breakdowns:

    1. Leaf accuracy, bucket a (the honest classifier number)
    2. Bucket b accuracy (boilerplate targets — architecture content, not classifier)
    3. Boundary rows: either listed node, or a correct low-confidence review
    4. Degraded rows: routed to BP.13 rather than auto-filed
    5. Domain and section accuracy — where in the tree it fails
    6. Accuracy by leaf-confidence band — the table that sets the threshold

Writes nothing. Reads bp_architecture and the answer key only.

    python scripts/evaluate_feed_classifier.py
"""

import csv
import logging
import os
import sys
from collections import Counter, defaultdict

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from supabase import create_client  # noqa: E402

from services.feed_classifier import (  # noqa: E402
    AUTO_FILE,
    PARENT_PARKED,
    REVIEW,
    classify_batch,
    load_index,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

KEY = os.path.join(PROJECT_ROOT, "evaluation", "classifier_answer_key_draft.csv")
BANDS = [(0.7, 1.01), (0.6, 0.7), (0.5, 0.6), (0.4, 0.5), (0.0, 0.4)]


def domain_of(node_id: str) -> str:
    """BP.X prefix."""
    return ".".join(node_id.split(".")[:2])


def section_of(node_id: str) -> str:
    """BP.X.Y prefix."""
    return ".".join(node_id.split(".")[:3])


def pct(n: int, d: int) -> str:
    """Format n/d as a percentage, tolerating d == 0."""
    return f"{n}/{d} ({100.0 * n / d:.1f}%)" if d else f"{n}/0 (n/a)"


def main() -> None:
    """Run the evaluation and print the report."""
    rows = list(csv.DictReader(open(KEY)))
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

    print("loading architecture index ...")
    index = load_index(sb)
    print(f"classifying {len(rows)} facts ...\n")
    routings = classify_batch([r["fact_text"] for r in rows], index)

    recs = []
    for row, rt in zip(rows, routings):
        targets = row["correct_node_id"].split("|")
        rec = {
            "row": row,
            "rt": rt,
            "targets": targets,
            "category": row["category"],
            "bucket": row["scoring_bucket"],
            "leaf_hit": rt.leaf.node_id in targets,
            "domain_hit": rt.domain.node_id in {domain_of(t) for t in targets},
            "section_hit": rt.section.node_id in {section_of(t) for t in targets},
        }
        recs.append(rec)

    a = [r for r in recs if r["bucket"] == "a_classifier"]
    b = [r for r in recs if r["bucket"] == "b_architecture_content"]
    primary = [r for r in a if r["category"] in ("clear", "hard")]

    print("=" * 78)
    print("FEED CLASSIFIER v1 — hierarchical embedding, no LLM judge")
    print("thresholds are PROVISIONAL placeholders (0.5); they are not tuned")
    print("=" * 78)

    # ---- 1 ----------------------------------------------------------------
    print("\n1. LEAF ACCURACY — bucket a (clear + hard). The honest number.")
    hits = sum(1 for r in primary if r["leaf_hit"])
    print(f"   exact correct_node_id: {pct(hits, len(primary))}")
    for cat in ("clear", "hard"):
        sub = [r for r in primary if r["category"] == cat]
        print(f"     {cat:<6} {pct(sum(1 for r in sub if r['leaf_hit']), len(sub))}")

    # ---- 2 ----------------------------------------------------------------
    print("\n2. BUCKET b — boilerplate targets (architecture content, not classifier)")
    bl = [r for r in b if r["category"] in ("clear", "hard")]
    print(f"   exact correct_node_id: {pct(sum(1 for r in bl if r['leaf_hit']), len(bl))}")
    print("   a low number here is an Alex content gap, not a classifier bug")

    # ---- 3 ----------------------------------------------------------------
    print("\n3. BOUNDARY ROWS — either listed node, or a correct low-confidence review")
    bnd = [r for r in recs if r["category"] == "boundary"]
    picked = [r for r in bnd if r["leaf_hit"]]
    reviewed = [r for r in bnd if not r["leaf_hit"] and r["rt"].decision == REVIEW]
    wrong = [r for r in bnd if not r["leaf_hit"] and r["rt"].decision != REVIEW]
    print(f"   acceptable: {pct(len(picked) + len(reviewed), len(bnd))}")
    print(f"     picked one of the two listed nodes : {len(picked)}")
    print(f"     routed to review (low confidence)  : {len(reviewed)}")
    print(f"     picked a THIRD node / parked       : {len(wrong)}")
    for r in wrong:
        print(f"       - {r['row']['correct_node_id']} -> got {r['rt'].leaf.node_id} "
              f"({r['rt'].decision}, leaf conf {r['rt'].leaf.confidence})")

    # ---- 4 ----------------------------------------------------------------
    print("\n4. DEGRADED ROWS — must route to BP.13, never auto-file")
    deg = [r for r in recs if r["category"] == "degraded"]
    ok = [r for r in deg if r["rt"].decision == REVIEW]
    print(f"   routed to review: {pct(len(ok), len(deg))}")
    print(f"   auto-filed (CONTRACT VIOLATION): "
          f"{sum(1 for r in deg if r['rt'].decision == AUTO_FILE)}")
    for r in deg:
        rt = r["rt"]
        flag = "ok " if rt.decision == REVIEW else "!! "
        print(f"     {flag}{r['row']['correct_node_id']:<12} decision={rt.decision:<14} "
              f"best_leaf={rt.leaf.node_id} degraded={rt.leaf_degraded} "
              f"({rt.leaf_degraded_reason})")

    # ---- 5 ----------------------------------------------------------------
    print("\n5. WHERE IT FAILS IN THE TREE (bucket a, clear + hard)")
    d_hit = sum(1 for r in primary if r["domain_hit"])
    s_hit = sum(1 for r in primary if r["section_hit"])
    print(f"   domain  correct: {pct(d_hit, len(primary))}")
    print(f"   section correct: {pct(s_hit, len(primary))}")
    print(f"   leaf    correct: {pct(hits, len(primary))}")
    lost = Counter()
    for r in primary:
        if r["leaf_hit"]:
            lost["correct at leaf"] += 1
        elif not r["domain_hit"]:
            lost["lost at DOMAIN"] += 1
        elif not r["section_hit"]:
            lost["lost at SECTION"] += 1
        else:
            lost["lost at LEAF (domain+section right)"] += 1
    for k, v in lost.most_common():
        print(f"     {k:<38} {v}")

    # ---- 6 ----------------------------------------------------------------
    print("\n6. ACCURACY BY LEAF-CONFIDENCE BAND (bucket a, clear + hard)")
    print("   this is the table that sets the auto-file threshold\n")
    print(f"   {'band':<14} {'n':>4} {'leaf correct':>16} {'domain correct':>16}")
    print("   " + "-" * 54)
    for lo, hi in BANDS:
        sub = [r for r in primary
               if r["rt"].leaf.confidence is not None
               and lo <= r["rt"].leaf.confidence < hi]
        if not sub:
            continue
        lh = sum(1 for r in sub if r["leaf_hit"])
        dh = sum(1 for r in sub if r["domain_hit"])
        label = f">= {lo}" if hi > 1 else f"{lo}-{hi}"
        print(f"   {label:<14} {len(sub):>4} {pct(lh, len(sub)):>16} {pct(dh, len(sub)):>16}")

    cum = sorted(primary, key=lambda r: -(r["rt"].leaf.confidence or 0))
    print("\n   cumulative (auto-file everything at or above a threshold):")
    print(f"   {'threshold':<12} {'auto-filed':>11} {'of those correct':>19}")
    print("   " + "-" * 44)
    for t in (0.75, 0.7, 0.65, 0.6, 0.55, 0.5, 0.45, 0.4):
        sel = [r for r in cum if (r["rt"].leaf.confidence or 0) >= t]
        if not sel:
            continue
        print(f"   {t:<12} {len(sel):>11} {pct(sum(1 for r in sel if r['leaf_hit']), len(sel)):>19}")

    # ---- decision mix -----------------------------------------------------
    print("\nDECISION MIX at the provisional 0.5 thresholds (all 76 rows)")
    for k, v in Counter(r["rt"].decision for r in recs).most_common():
        print(f"   {k:<16} {v}")

    print("\n" + "=" * 78)
    print("Nothing written. No tuning applied, no LLM judge.")


if __name__ == "__main__":
    main()
