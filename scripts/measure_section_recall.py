#!/usr/bin/env python3
"""Section-level recall — is the correct sibling group reachable, even when the leaf isn't?

The leaf-level ceiling is recall@10 = 44-46%. This asks a different question: if
we only need the right PARENT (the sibling group the judge would then compare
against), how often is it in the top few?

Two ways of getting there are measured, because they are not the same thing:

  induced  rank the leaves by similarity, map each to its parent, dedupe in
           order -> where does the correct parent appear?
  direct   score the fact against the section nodes' OWN embeddings -> where
           does the correct one rank? (this is what classifier v1 did at hop 2,
           and it failed; measured again for comparison)

If induced section recall is high, section-first + judge-over-all-siblings is
viable: the answer is guaranteed present in the candidate set, which removes the
"answer not in top-10" ceiling entirely.

Writes nothing.

    python scripts/measure_section_recall.py
"""

import csv
import json
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
from services.feed_classifier_v2 import load_leaf_ids  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

CACHE = "/home/saiaditya26122006/.claude/jobs/bdcd467b/tmp"
KEY = os.path.join(PROJECT_ROOT, "evaluation", "classifier_answer_key_draft.csv")

PARAPHRASES = [
    ("BP.5.3.3", "deans have ~25k discretion, above that it's committee"),
    ("BP.5.1.2", "the dean is who actually buys, not the researcher"),
    ("BP.7.2.3", "we don't train on their papers. ever. no fine-tuning either"),
    ("BP.10.1.3", "3 depts, 12 wks, 40ish papers each"),
    ("BP.4.1.3", "no corporate labs. universities only"),
    ("BP.9.1.1", "point is the supervisor doesn't have to find the weak claims himself"),
    ("BP.5.1.4", "security guy has no budget but can kill a deal for months"),
    ("BP.8.1.5", "grammarly/elicit aren't competitors exactly but same money pot"),
    ("BP.12.1.4", "1-5 impact, 1-5 likelihood, that's the scoring"),
    ("BP.6.1.4", "same 12 qs every call so we can compare across unis"),
]


def parent_of(node_id: str) -> str:
    """The sibling group a leaf belongs to."""
    return ".".join(node_id.split(".")[:-1])


def vecs_of(rows, key="embedding"):
    """Stack and L2-normalise embeddings from rows."""
    mat = np.vstack([
        np.asarray(json.loads(r[key]) if isinstance(r[key], str) else r[key],
                   dtype=np.float32)
        for r in rows
    ])
    return mat / np.linalg.norm(mat, axis=1, keepdims=True)


def main() -> None:
    """Measure induced and direct section recall on both fact sets."""
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    leaf_ids = load_leaf_ids(sb)

    rows, start = [], 0
    while True:
        r = (
            sb.table("bp_architecture")
            .select("node_id,node_title,purpose,required_output,degraded_target,embedding")
            .range(start, start + 99)
            .execute()
        )
        if not r.data:
            break
        rows += r.data
        start += 100
    by_id = {r["node_id"]: r for r in rows}

    import re
    BP = re.compile(r"^Governs .+ without validating downstream commercial success\.?$", re.I)
    BR = re.compile(r"^.+ specification and governance\.?$", re.I)

    def thin(r):
        return (
            (BP.match((r.get("purpose") or "").strip())
             and BR.match((r.get("required_output") or "").strip()))
            or r["degraded_target"]
            or not (r.get("required_output") or "").strip()
        )

    pool = [r for r in rows
            if r["node_id"] in leaf_ids and not thin(r) and r["embedding"] is not None]
    pool_ids = [r["node_id"] for r in pool]
    base_mat = vecs_of(pool)

    enriched = {}
    path = os.path.join(CACHE, "scope_emb_B.json")
    if os.path.exists(path):
        enriched = json.load(open(path))

    # sibling-group sizes over the content-complete pool
    groups = Counter(parent_of(n) for n in pool_ids)
    sizes = sorted(groups.values())
    print(f"content-complete leaves: {len(pool_ids)} in {len(groups)} sibling groups")
    print(f"  group size: min={sizes[0]} median={sizes[len(sizes)//2]} "
          f"p90={sizes[int(len(sizes)*0.9)]} max={sizes[-1]}")
    print(f"  groups with >15 siblings: {sum(1 for v in sizes if v > 15)}")

    key = list(csv.DictReader(open(KEY)))
    bucket_a = [
        (r["correct_node_id"], r["fact_text"]) for r in key
        if r["scoring_bucket"] == "a_classifier" and r["category"] in ("clear", "hard")
    ]
    para = [(t, f) for t, f in PARAPHRASES]

    sections = [r for r in rows if r["node_id"].count(".") == 2 and r["embedding"]]
    sec_ids = [r["node_id"] for r in sections]
    sec_mat = vecs_of(sections)

    def report(label, facts, mat, ids, use_enriched=False):
        if use_enriched:
            keep = [n for n in ids if n in enriched]
            mat = np.vstack([np.asarray(enriched[n], dtype=np.float32) for n in keep])
            mat /= np.linalg.norm(mat, axis=1, keepdims=True)
            ids = keep
        ind = {1: 0, 2: 0, 3: 0, 5: 0}
        dirr = {1: 0, 3: 0, 5: 0}
        detail = []
        for target, text in facts:
            q = np.asarray(embed(text, input_type="search_query"), dtype=np.float32)
            q /= np.linalg.norm(q)
            tgts = [t for t in target.split("|") if t in ids]
            want = {parent_of(t) for t in tgts}
            if not want:
                continue
            order = np.argsort(-(mat @ q))
            seen, srank = [], None
            for j in order:
                p = parent_of(ids[int(j)])
                if p not in seen:
                    seen.append(p)
                if p in want and srank is None:
                    srank = len(seen)
            for k in ind:
                if srank and srank <= k:
                    ind[k] += 1
            # direct: section nodes' own embeddings
            so = np.argsort(-(sec_mat @ q))
            dr = next((i + 1 for i, j in enumerate(so) if sec_ids[int(j)] in want), None)
            for k in dirr:
                if dr and dr <= k:
                    dirr[k] += 1
            detail.append((target, srank, dr, groups.get(list(want)[0], 0)))
        n = len(detail)
        print(f"\n{label}  (n={n})")
        print("  induced (via top leaves): " +
              "  ".join(f"@{k}={100.0*ind[k]/n:.1f}%" for k in sorted(ind)))
        print("  direct  (section vector): " +
              "  ".join(f"@{k}={100.0*dirr[k]/n:.1f}%" for k in sorted(dirr)))
        return detail

    print("\n" + "=" * 78)
    print("SECTION RECALL — is the correct sibling group reachable?")
    print("=" * 78)
    report("bucket-a facts, baseline embeddings", bucket_a, base_mat, pool_ids)
    if enriched:
        report("bucket-a facts, enriched (variant B)", bucket_a, base_mat, pool_ids, True)
    d = report("oblique paraphrases, baseline", para, base_mat, pool_ids)
    if enriched:
        report("oblique paraphrases, enriched (variant B)", para, base_mat, pool_ids, True)

    print("\n  per-paraphrase induced section rank (baseline):")
    for target, srank, dr, size in d:
        p = parent_of(target)
        print(f"    section rank {str(srank):>5}   direct {str(dr):>5}   "
              f"{p:<11} ({groups.get(p, 0)} siblings)")

    print("\nNothing written.")


if __name__ == "__main__":
    main()
