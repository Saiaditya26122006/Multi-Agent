#!/usr/bin/env python3
"""Recall@k on a cleaned candidate pool, plus what the distractors actually are.

Measures three pools over the same 54 bucket-a facts:

    full            every childless leaf
    -boilerplate    minus the ~35 non-discriminating BP.9 nodes
    -boiler -degr   also minus every degraded node

and reports what is actually crowding the top-k in the misses, so the fix is
aimed at the real distractors rather than assumed ones.

Writes nothing.

    python scripts/measure_recall_clean_pool.py
"""

import csv
import json
import logging
import os
import re
import sys
from collections import Counter

import numpy as np
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from supabase import create_client  # noqa: E402

from services.embedding_service import embed  # noqa: E402
from services.feed_classifier_v2 import load_leaf_ids, section_of  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

KEY = os.path.join(PROJECT_ROOT, "evaluation", "classifier_answer_key_draft.csv")
KS = (1, 3, 5, 10, 20, 40)

BOIL_P = re.compile(r"^Governs .+ without validating downstream commercial success\.?$", re.I)
BOIL_R = re.compile(r"^.+ specification and governance\.?$", re.I)


def is_boilerplate(row: dict) -> bool:
    """True when purpose AND required_output are the non-discriminating template."""
    return bool(
        BOIL_P.match((row.get("purpose") or "").strip())
        and BOIL_R.match((row.get("required_output") or "").strip())
    )


def domain_of(node_id: str) -> str:
    """BP.X prefix."""
    return ".".join(node_id.split(".")[:2])


def rank_facts(facts, ids, mat, pos):
    """Return (hits by k, ranks, misses) for one candidate pool."""
    hits = {k: 0 for k in KS}
    ranks, misses = [], []
    for r, q in facts:
        sims = mat @ q
        order = np.argsort(-sims)
        ranked = [ids[int(i)] for i in order]
        targets = [t for t in r["correct_node_id"].split("|") if t in pos]
        best = min((ranked.index(t) + 1 for t in targets), default=10**6)
        ranks.append(best)
        for k in KS:
            if best <= k:
                hits[k] += 1
        if best > 10:
            misses.append((r, ranked[:5], sims, order, best))
    return hits, ranks, misses


def main() -> None:
    """Measure the three pools and analyse distractors."""
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    leaf_ids = load_leaf_ids(sb)

    print("loading nodes ...")
    rows, start = [], 0
    while True:
        resp = (
            sb.table("bp_architecture")
            .select("node_id,node_title,degraded_target,degraded_reason,purpose,"
                    "required_output,embedding")
            .not_.is_("embedding", "null")
            .range(start, start + 99)
            .execute()
        )
        if not resp.data:
            break
        rows += resp.data
        start += 100

    leaves = [r for r in rows if r["node_id"] in leaf_ids]
    for r in leaves:
        r["_boiler"] = is_boilerplate(r)
    boiler_ids = {r["node_id"] for r in leaves if r["_boiler"]}
    degraded_ids = {r["node_id"] for r in leaves if r["degraded_target"]}
    meta = {r["node_id"]: r for r in leaves}

    print(f"leaf pool: {len(leaves)} | boilerplate: {len(boiler_ids)} | "
          f"degraded: {len(degraded_ids)} | overlap: {len(boiler_ids & degraded_ids)}")

    key = list(csv.DictReader(open(KEY)))
    sel = [r for r in key
           if r["scoring_bucket"] == "a_classifier" and r["category"] in ("clear", "hard")]
    print(f"embedding {len(sel)} bucket-a facts ...\n")
    facts = []
    for r in sel:
        q = np.asarray(embed(r["fact_text"], input_type="search_query"), dtype=np.float32)
        facts.append((r, q / np.linalg.norm(q)))

    def build(exclude: set):
        keep = [r for r in leaves if r["node_id"] not in exclude]
        m = np.vstack([
            np.asarray(json.loads(r["embedding"]) if isinstance(r["embedding"], str)
                       else r["embedding"], dtype=np.float32) for r in keep])
        m /= np.linalg.norm(m, axis=1, keepdims=True)
        i = [r["node_id"] for r in keep]
        return i, m, {n: j for j, n in enumerate(i)}

    pools = [
        ("full pool", set()),
        ("minus boilerplate", boiler_ids),
        ("minus boilerplate + degraded", boiler_ids | degraded_ids),
    ]

    n = len(facts)
    results = {}
    print("=" * 78)
    print(f"RECALL@K — {n} bucket-a facts (clear + hard)")
    print("=" * 78)
    print(f"{'pool':<30}{'size':>6}" + "".join(f"{'@' + str(k):>8}" for k in KS))
    print("-" * 78)
    for label, excl in pools:
        ids, mat, pos = build(excl)
        hits, ranks, misses = rank_facts(facts, ids, mat, pos)
        results[label] = (hits, ranks, misses, ids, pos)
        line = f"{label:<30}{len(ids):>6}"
        line += "".join(f"{100.0 * hits[k] / n:7.1f}%" for k in KS)
        print(line)

    # ---- what is actually crowding the top-k, on the FULL pool -----------
    _, _, misses, ids, pos = results["full pool"]
    print(f"\n{'=' * 78}\nWHAT IS ACTUALLY IN THE TOP-3 OF THE {len(misses)} MISSES "
          f"(full pool)\n{'=' * 78}")
    kinds: Counter = Counter()
    branch: Counter = Counter()
    misses_with_boiler = 0
    for r, top5, sims, order, best in misses:
        has_boiler = False
        for nid in top5[:3]:
            m = meta[nid]
            if m["_boiler"]:
                kinds["boilerplate"] += 1
                branch[".".join(nid.split(".")[:3])] += 1
                has_boiler = True
            elif m["degraded_target"]:
                kinds["degraded (not boilerplate)"] += 1
            else:
                kinds["trusted, real content"] += 1
        misses_with_boiler += has_boiler
    tot = sum(kinds.values()) or 1
    for k, v in kinds.most_common():
        print(f"  {k:<28} {v:>3}  ({100.0 * v / tot:.0f}% of top-3 slots)")
    print(f"\n  misses with ANY boilerplate node in their top-3: "
          f"{misses_with_boiler}/{len(misses)}")
    if branch:
        print(f"  boilerplate distractors by branch: {dict(branch)}")

    # ---- remaining misses on the cleanest pool --------------------------
    label = "minus boilerplate + degraded"
    hits, ranks, misses, ids, pos = results[label]
    print(f"\n{'=' * 78}\nREMAINING MISSES ON THE CLEAN POOL — {len(misses)} facts "
          f"outside top-10\n{'=' * 78}")
    in40 = sum(1 for r in ranks if 10 < r <= 40)
    beyond = sum(1 for r in ranks if r > 40)
    print(f"  correct node in top-40 (ranking problem, judge-fixable): {in40}")
    print(f"  correct node beyond top-40 (embedding problem)         : {beyond}")
    deep = sorted(
        ((r["correct_node_id"], rk, r["fact_text"]) for (r, _, _, _, rk) in misses),
        key=lambda t: -t[1],
    )[:8]
    print("\n  deepest remaining misses:")
    for nid, rk, fact in deep:
        print(f"    rank {rk:>4}  {nid:<12} {fact[:52]!r}")

    print("\nNothing written. No judge, no tuning.")


if __name__ == "__main__":
    main()
