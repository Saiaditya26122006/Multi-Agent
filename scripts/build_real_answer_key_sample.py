#!/usr/bin/env python3
"""Pull a stratified sample of a real Feed run for hand-labelling.

The 78-fact key is written test material. This produces the same thing from a
real document, so auto-file precision can be measured on real phrasing.

**The labelling is Alex's / the reviewer's. This script does not guess a correct
node, and deliberately does not rank the candidates by anything the classifier
believed — the proposed node is shown alongside the alternatives, not above
them, so the label is formed from the architecture rather than anchored to the
classifier's answer.**

Sampled across all four routing outcomes, not just the committed ones. Precision
alone would say nothing about whether the 39 "none fit" refusals were correct,
and that is half of question (4): is the classifier refusing a genuinely hard
document, or failing on facts it should have caught?

Output: evaluation/real_answer_key_sample.csv with `correct_node_id` blank.

Writes nothing to any datastore.

    python scripts/build_real_answer_key_sample.py [run_id]
"""

import csv
import logging
import os
import random
import sys

import numpy as np
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from supabase import create_client  # noqa: E402

from services.embedding_service import embed  # noqa: E402
from services.feed_classifier_v3 import (  # noqa: E402
    load_architecture,
    rank_sections,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

DEFAULT_RUN = "feed-225ea932cc7d"
OUT = os.path.join(PROJECT_ROOT, "evaluation", "real_answer_key_sample.csv")
SEED = 20260728
TOP_SECTIONS = 3

# Stratum -> how many rows to sample. Weighted toward the committed facts (they
# are the precision denominator) while keeping enough refusals to tell a correct
# refusal from a miss.
QUOTA = {
    "would_auto_file": 22,
    "no_fitting_node": 10,
    "judge_unsure": 4,
    "node_degraded": 4,
}


def stratum(meta: dict) -> str:
    """Which routing outcome a stored fact fell into."""
    if meta.get("degraded_target"):
        return "node_degraded"
    if meta.get("judge_choice") in (None, "none"):
        return "no_fitting_node"
    if meta.get("judge_confidence") == "high":
        return "would_auto_file"
    return "judge_unsure"


def main() -> None:
    """Sample a real run and write a blank-labelled answer key."""
    run_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RUN
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

    rows, start = [], 0
    while True:
        resp = (
            sb.table("knowledge_base")
            .select("id,content,section,metadata")
            .eq("run_id", run_id)
            .range(start, start + 99)
            .execute()
        )
        if not resp.data:
            break
        rows += resp.data
        start += 100
    if not rows:
        print(f"no stored rows for run_id={run_id}")
        sys.exit(1)
    print(f"run {run_id}: {len(rows)} stored facts")

    buckets: dict[str, list] = {}
    for row in rows:
        buckets.setdefault(stratum(row["metadata"]), []).append(row)
    print("strata: " + ", ".join(f"{k}={len(v)}" for k, v in sorted(buckets.items())))

    random.seed(SEED)
    sample = []
    for name, quota in QUOTA.items():
        pool = buckets.get(name, [])
        take = min(quota, len(pool))
        sample += [(name, r) for r in random.sample(pool, take)]
        if take < quota:
            print(f"  note: only {take} available in stratum {name} (wanted {quota})")

    arch = load_architecture(sb)
    title = lambda n: (arch.nodes.get(n) or {}).get("node_title") or "?"

    print(f"\nembedding {len(sample)} facts to recover candidate sections ...")
    out_rows = []
    for name, row in sample:
        meta = row["metadata"]
        vector = embed(row["content"], input_type="search_query")
        sections = rank_sections(vector, arch, top_n=TOP_SECTIONS)

        record = {
            "fact_text": row["content"],
            "correct_node_id": "",          # <- Alex fills this in
            "notes": "",                    # <- and this
            "stratum": name,
            "proposed_node_id": meta.get("judge_choice") or "",
            "proposed_node_title": title(meta.get("judge_choice")),
            "judge_confidence": meta.get("judge_confidence") or "",
            "decision": meta.get("decision"),
            "review_reasons": "|".join(meta.get("review_reasons") or []),
            "degraded_target": meta.get("degraded_target"),
            "degraded_reason": meta.get("degraded_reason") or "",
            "section_margin": meta.get("section_margin"),
            "source_quote": meta.get("source_quote"),
            "span": (
                f"{meta.get('start_char')}:{meta.get('end_char')}"
                if meta.get("start_char") is not None
                else "no span"
            ),
            "kb_row_id": row["id"],
        }
        for i in range(TOP_SECTIONS):
            if i < len(sections):
                section = sections[i]
                leaves = arch.siblings.get(section.section_id, [])
                record[f"cand{i + 1}_section"] = section.section_id
                record[f"cand{i + 1}_title"] = title(section.section_id)
                record[f"cand{i + 1}_sim"] = section.best_leaf_similarity
                record[f"cand{i + 1}_leaves"] = "; ".join(
                    f"{n} {title(n)}" for n in leaves
                )
            else:
                for suffix in ("section", "title", "sim", "leaves"):
                    record[f"cand{i + 1}_{suffix}"] = ""
        out_rows.append(record)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(out_rows[0]))
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"\nwrote {len(out_rows)} rows -> {OUT}")
    print("  correct_node_id and notes are BLANK — they are yours to fill in.")
    print("  Each row lists the top 3 candidate sections and every leaf in them,")
    print("  so the correct node can be chosen without trusting the proposal.")
    print("\n  strata in the sample:")
    for name in QUOTA:
        count = sum(1 for r in out_rows if r["stratum"] == name)
        print(f"    {name:<18} {count}")
    print("\nNothing written to knowledge_base or bp_architecture.")


if __name__ == "__main__":
    main()
