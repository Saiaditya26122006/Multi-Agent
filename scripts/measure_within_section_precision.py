#!/usr/bin/env python3
"""When the right SECTION is retrieved, how often does the judge pick the right leaf?

This is the ceiling on any work that improves judge discrimination — slot
matching, statement-type tagging, better sibling descriptions. None of it can
help a fact whose correct section was never retrieved, so the honest denominator
is not "all facts" but "facts whose correct section reached the candidate pool".

Every number in the LOCKED CONCLUSION is leaf recall@k — a retrieval metric.
`feed_classifier_v3`'s own docstring names the other half as untested: "Coverage
is expected to be low; precision is the open question." This measures that half.

Two stages, reported separately so they cannot be conflated:

    stage 1  is the correct node's SECTION among the retrieved sections?
    stage 2  given it is, does propose() rank the correct LEAF first / at all?

⚠️ The labelled key is REWRITTEN-ERA. Its facts are self-contained sentences of
the kind the old chunker produced; the pipeline now stores verbatim segments,
which retrieve measurably worse (see PROJECT_STATE, "VERBATIM EXTRACTION COSTS
RETRIEVAL"). Stage 1 here is therefore optimistic. Stage 2 — the number this
script exists for — is far less affected, because it is conditioned on retrieval
having already worked.

    python scripts/measure_within_section_precision.py
    python scripts/measure_within_section_precision.py --limit 20

Writes evaluation/within_section_precision.json.
"""

import argparse
import csv
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from services.embedding_service import embed  # noqa: E402
from services.feed_classifier_v3 import (  # noqa: E402
    load_architecture,
    parent_of,
    propose,
    rank_sections,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

KEY = os.path.join(PROJECT_ROOT, "evaluation", "classifier_answer_key_draft.csv")
OUT = os.path.join(PROJECT_ROOT, "evaluation", "within_section_precision.json")
SECTIONS_TO_CONSIDER = 3
MAX_WORKERS = 6


def load_key(limit: Optional[int]) -> list[dict[str, str]]:
    """Labelled (fact, correct node) rows that name a real node."""
    rows: list[dict[str, str]] = []
    with open(KEY, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            fact = (row.get("fact_text") or "").strip()
            node = (row.get("correct_node_id") or "").strip()
            if fact and node.startswith("BP."):
                rows.append(
                    {"fact": fact, "node_id": node, "bucket": row.get("scoring_bucket")}
                )
    return rows[:limit] if limit else rows


def score(row: dict[str, str], arch: Any) -> dict[str, Any]:
    """Both stages for one labelled fact."""
    correct = row["node_id"]
    want_section = correct if arch.siblings.get(correct) else parent_of(correct)
    out: dict[str, Any] = {**row, "correct_section": want_section}

    try:
        vector = embed(row["fact"], input_type="search_query")
    except Exception as exc:  # noqa: BLE001 — recorded, not silently dropped
        return {**out, "error": f"embed failed: {str(exc)[:120]}"}

    sections = rank_sections(vector, arch, top_n=max(SECTIONS_TO_CONSIDER, 2))
    retrieved = [s.section_id for s in sections[:SECTIONS_TO_CONSIDER]]
    out["retrieved_sections"] = retrieved
    out["section_retrieved"] = want_section in retrieved
    if not out["section_retrieved"]:
        return out

    # Stage 2 only runs where stage 1 succeeded — that is the whole point.
    try:
        proposal = propose(
            row["fact"],
            arch,
            fact_vector=vector,
            sections_to_consider=SECTIONS_TO_CONSIDER,
        )
    except Exception as exc:  # noqa: BLE001
        return {**out, "error": f"propose failed: {str(exc)[:120]}"}

    shortlist = [c.node_id for c in proposal.candidates]
    out["shortlist"] = shortlist
    out["rank_of_correct"] = (
        shortlist.index(correct) + 1 if correct in shortlist else None
    )
    out["correct_at_1"] = bool(shortlist) and shortlist[0] == correct
    out["correct_in_shortlist"] = correct in shortlist
    return out


def main() -> None:
    """Measure and print both stages."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    arch = load_architecture()
    rows = load_key(args.limit)
    print(f"{len(rows)} labelled fact(s) from {os.path.basename(KEY)}")

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(score, r, arch) for r in rows]
        for done in as_completed(futures):
            results.append(done.result())

    errors = [r for r in results if "error" in r]
    scored = [r for r in results if "error" not in r]
    retrieved = [r for r in scored if r["section_retrieved"]]
    at1 = [r for r in retrieved if r.get("correct_at_1")]
    inlist = [r for r in retrieved if r.get("correct_in_shortlist")]

    print()
    print("=" * 74)
    print("STAGE 1 — retrieval (not what slot work can move)")
    print(f"  facts scored                    : {len(scored)}")
    print(f"  correct section in top-{SECTIONS_TO_CONSIDER}         : "
          f"{len(retrieved)}  ({len(retrieved) / len(scored) * 100:.1f}%)")
    print()
    print("STAGE 2 — judge precision GIVEN the right section was retrieved")
    print("  ** this is the ceiling slot matching can move **")
    if retrieved:
        print(f"  correct leaf ranked 1st         : {len(at1)}/{len(retrieved)}"
              f"  ({len(at1) / len(retrieved) * 100:.1f}%)")
        print(f"  correct leaf anywhere in list   : {len(inlist)}/{len(retrieved)}"
              f"  ({len(inlist) / len(retrieved) * 100:.1f}%)")
        print(f"  HEADROOM at rank 1              : "
              f"{(len(retrieved) - len(at1)) / len(retrieved) * 100:.1f} pts")
    print(f"  errors                          : {len(errors)}")
    print("=" * 74)

    misses = [r for r in retrieved if not r.get("correct_at_1")]
    if misses:
        print("\nright section, wrong leaf (what slot matching would target):")
        for m in misses[:12]:
            got = (m.get("shortlist") or ["-"])[0]
            print(f"  want {m['node_id']:12} got {got:12} rank="
                  f"{m.get('rank_of_correct')}  {m['fact'][:52]}")

    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "scored": len(scored),
                "section_retrieved": len(retrieved),
                "correct_at_1": len(at1),
                "correct_in_shortlist": len(inlist),
                "results": results,
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
