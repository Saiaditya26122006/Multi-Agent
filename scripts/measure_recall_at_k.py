#!/usr/bin/env python3
"""Recall@k for Feed retrieval — does the correct node reach the shortlist?

The decisive question before building an LLM judge. A judge can only pick from
what retrieval hands it:

  * high recall@10  -> embeddings rank badly but retrieve well; a judge picks
                       from a good shortlist and the ceiling is high
  * low  recall@10  -> the answer is not in the set; no judge can recover it,
                       and the problem is retrieval, not selection

For every miss it also reports the correct node's true rank and what outranked
it, classified structurally (same section / same domain / unrelated) so "hard
problem" and "broken retrieval" are distinguishable.

Writes nothing.

    python scripts/measure_recall_at_k.py
"""

import csv
import logging
import os
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
KS = (1, 3, 5, 10, 20, 50)


def domain_of(node_id: str) -> str:
    """BP.X prefix."""
    return ".".join(node_id.split(".")[:2])


def relation(candidate: str, correct: str) -> str:
    """Structural closeness of a distractor to the correct node."""
    if section_of(candidate) == section_of(correct):
        return "same section"
    if domain_of(candidate) == domain_of(correct):
        return "same domain"
    return "UNRELATED"


def main() -> None:
    """Compute recall@k and analyse the misses."""
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    leaf_ids = load_leaf_ids(sb)

    print("loading leaf embeddings ...")
    rows, start = [], 0
    while True:
        resp = (
            sb.table("bp_architecture")
            .select("node_id,node_title,degraded_target,embedding")
            .not_.is_("embedding", "null")
            .range(start, start + 99)
            .execute()
        )
        if not resp.data:
            break
        rows += resp.data
        start += 100

    import json

    leaves = [r for r in rows if r["node_id"] in leaf_ids]
    mat = np.vstack(
        [
            np.asarray(
                json.loads(r["embedding"]) if isinstance(r["embedding"], str)
                else r["embedding"],
                dtype=np.float32,
            )
            for r in leaves
        ]
    )
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)
    ids = [r["node_id"] for r in leaves]
    titles = {r["node_id"]: r["node_title"] for r in leaves}
    pos = {n: i for i, n in enumerate(ids)}
    print(f"leaf pool: {len(ids)} childless nodes\n")

    key = list(csv.DictReader(open(KEY)))
    facts = [
        r for r in key
        if r["scoring_bucket"] == "a_classifier" and r["category"] in ("clear", "hard")
    ]

    hits = {k: 0 for k in KS}
    ranks: list[int] = []
    misses = []

    for r in facts:
        q = np.asarray(embed(r["fact_text"], input_type="search_query"), dtype=np.float32)
        q /= np.linalg.norm(q)
        sims = mat @ q
        order = np.argsort(-sims)
        ranked = [ids[int(i)] for i in order]

        targets = r["correct_node_id"].split("|")
        best_rank = min(
            (ranked.index(t) + 1 for t in targets if t in pos), default=10**6
        )
        ranks.append(best_rank)
        for k in KS:
            if best_rank <= k:
                hits[k] += 1

        if best_rank > 10:
            correct = targets[0]
            misses.append(
                {
                    "fact": r["fact_text"],
                    "correct": correct,
                    "correct_title": titles.get(correct, "?"),
                    "correct_rank": best_rank,
                    "correct_sim": round(float(sims[pos[correct]]), 4)
                    if correct in pos else None,
                    "top3": [
                        (
                            ranked[i],
                            titles.get(ranked[i], "?"),
                            round(float(sims[order[i]]), 4),
                            relation(ranked[i], correct),
                        )
                        for i in range(3)
                    ],
                }
            )

    n = len(facts)
    print("=" * 78)
    print(f"RECALL@K — {n} bucket-a facts (clear + hard), {len(ids)}-leaf pool")
    print("=" * 78)
    for k in KS:
        bar = "#" * int(40 * hits[k] / n)
        print(f"  recall@{k:<3} {hits[k]:>3}/{n}  {100.0 * hits[k] / n:5.1f}%  {bar}")

    finite = [r for r in ranks if r < 10**6]
    finite.sort()
    print(f"\n  median rank of the correct node : {finite[len(finite) // 2]}")
    print(f"  mean rank                        : {sum(finite) / len(finite):.1f}")
    print(f"  worst rank                       : {max(finite)}")
    print(f"  never retrieved at all           : {len(ranks) - len(finite)}")

    print("\n" + "=" * 78)
    print(f"MISS ANALYSIS — {len(misses)} facts where the correct node is NOT in top-10")
    print("=" * 78)

    rel_counter: Counter = Counter()
    for m in misses:
        print(f"\n  fact: {m['fact'][:70]!r}")
        print(f"  correct: {m['correct']} {m['correct_title'][:44]!r}")
        print(f"           rank {m['correct_rank']} of {len(ids)}, sim {m['correct_sim']}")
        print("  outranked by:")
        for nid, title, sim, rel in m["top3"]:
            rel_counter[rel] += 1
            print(f"    {sim:.4f}  {nid:<13} {str(title)[:40]:<42} [{rel}]")

    print("\n" + "-" * 78)
    print("distractor relation to the correct node (top-3 of every miss):")
    total = sum(rel_counter.values()) or 1
    for rel, c in rel_counter.most_common():
        print(f"  {rel:<14} {c:>3}  ({100.0 * c / total:.0f}%)")
    print("\n  'same section'/'same domain' = hard discrimination problem")
    print("  'UNRELATED'                  = retrieval is off, not just ranking")
    print("\nNothing written. No judge, no tuning.")


if __name__ == "__main__":
    main()
