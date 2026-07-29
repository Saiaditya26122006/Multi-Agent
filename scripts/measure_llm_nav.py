#!/usr/bin/env python3
"""Measure LLM-navigated classification against the embedding path.

Reports, per hop, WHERE facts are lost — the diagnostic that mattered for v1,
which lost 40 of 54 facts at the domain hop by cosine similarity. If LLM
reasoning fixes hop one, that shows up here immediately.

Scored on the two labelled fact sets. The real-document facts are run too, but
for routing behaviour, latency and cost ONLY — they have no ground-truth labels
yet (`evaluation/real_answer_key_sample.csv` is unfilled), so precision on them
is not computable and is not reported.

Writes nothing.

    python scripts/measure_llm_nav.py [--real N]
"""

import argparse
import csv
import json
import logging
import os
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from services.feed_classifier_llm_nav import NONE_FIT, navigate  # noqa: E402
from services.feed_classifier_v3 import load_architecture, parent_of  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

KEY = os.path.join(PROJECT_ROOT, "evaluation", "classifier_answer_key_draft.csv")
REAL = os.path.join(PROJECT_ROOT, "evaluation", "real_answer_key_sample.csv")
OUT = os.path.join(os.getenv("CLAUDE_JOB_DIR", "/tmp"), "tmp", "llm_nav_results.json")

MAX_WORKERS = 6
CALL_TIMEOUT = 600
TOTAL_TIMEOUT = 7200

# Anthropic first-party Sonnet rates, USD per 1M tokens. Bedrock is partner-priced;
# confirm against the AWS price list before quoting a bill.
USD_IN_PER_M = 3.00
USD_OUT_PER_M = 15.00

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


def domain_of(node_id: str) -> str:
    """BP.X prefix."""
    return ".".join(node_id.split(".")[:2])


def load_labelled() -> dict[str, list[tuple[list[str], str]]]:
    """The two fact sets that have ground truth."""
    rows = list(csv.DictReader(open(KEY)))
    return {
        "bucket-a": [
            (r["correct_node_id"].split("|"), r["fact_text"])
            for r in rows
            if r["scoring_bucket"] == "a_classifier"
            and r["category"] in ("clear", "hard")
        ],
        "paraphrase": [([t], f) for t, f in PARAPHRASES],
    }


def score(items, results: dict, label: str) -> None:
    """Report per-hop survival and auto-file precision for one fact set."""
    n = domain_ok = section_ok = leaf_ok = 0
    committed = correct = 0
    lost = Counter()
    for targets, text in items:
        r = results.get(text)
        if r is None:
            continue
        n += 1
        want_domains = {domain_of(t) for t in targets}
        want_sections = {parent_of(t) for t in targets}

        if r["domain"] in want_domains:
            domain_ok += 1
        else:
            lost["domain"] += 1
            continue
        if r["section"] in want_sections:
            section_ok += 1
        else:
            lost["section"] += 1
            continue
        if r["leaf"] in targets:
            leaf_ok += 1
        elif r["leaf"] in (None, NONE_FIT):
            lost["leaf (refused)"] += 1
        else:
            lost["leaf (wrong sibling)"] += 1

    for targets, text in items:
        r = results.get(text)
        if r is None or r["leaf"] in (None, NONE_FIT):
            continue
        committed += 1
        correct += r["leaf"] in targets

    print(f"\n{label}  (n={n})")
    print(f"  hop 1  correct DOMAIN  : {domain_ok}/{n}  ({100.0 * domain_ok / n:.1f}%)")
    print(f"  hop 2  correct SECTION : {section_ok}/{n}  ({100.0 * section_ok / n:.1f}%)")
    print(f"  hop 3  correct LEAF    : {leaf_ok}/{n}  ({100.0 * leaf_ok / n:.1f}%)")
    print(f"  where facts were lost  : {dict(lost)}")
    print(f"  committed (not 'none') : {committed}/{n}  ({100.0 * committed / n:.1f}%)")
    if committed:
        print(f"  ** AUTO-FILE PRECISION : {correct}/{committed}  "
              f"({100.0 * correct / committed:.1f}%) **")
    else:
        print("  ** AUTO-FILE PRECISION : n/a (nothing committed) **")


def run(facts: list[str], arch) -> dict[str, dict]:
    """Navigate every fact, in parallel across facts (hops stay sequential)."""
    out: dict[str, dict] = {}
    failures = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(navigate, f, arch): f for f in facts}
        done = 0
        for future in as_completed(futures, timeout=TOTAL_TIMEOUT):
            try:
                result = future.result(timeout=CALL_TIMEOUT)
                out[result.fact] = result.to_dict()
            except Exception as exc:  # noqa: BLE001 — counted and reported
                failures += 1
                logging.error("navigate failed: %s", str(exc)[:180])
            done += 1
            if done % 20 == 0:
                print(f"    {done}/{len(facts)}")
    if failures:
        print(f"  !! {failures} facts failed to navigate")
    return out


def main() -> None:
    """Run both labelled sets plus a real-fact sample, and report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", type=int, default=20,
                        help="how many real-document facts to run (unlabelled)")
    args = parser.parse_args()

    arch = load_architecture()
    labelled = load_labelled()

    all_facts = [t for items in labelled.values() for _, t in items]
    real_facts: list[str] = []
    if args.real and os.path.exists(REAL):
        rows = list(csv.DictReader(open(REAL)))
        real_facts = [r["fact_text"] for r in rows][: args.real]
        all_facts += real_facts

    print(f"navigating {len(all_facts)} facts "
          f"({len(all_facts) - len(real_facts)} labelled + {len(real_facts)} real) "
          f"at {MAX_WORKERS} workers, 3 LLM hops each ...")
    results = run(all_facts, arch)
    json.dump(results, open(OUT, "w"), indent=1)

    print("\n" + "=" * 78)
    print("LLM-NAVIGATED CLASSIFICATION — no embeddings in the path")
    print("=" * 78)
    for name, items in labelled.items():
        score(items, results, name)

    # ---- cost and latency, over everything that ran -----------------------
    per_fact = [r["seconds"] for r in results.values()]
    hops = [s for r in results.values() for s in r["steps"]]
    by_level: dict[str, list[float]] = {}
    for step in hops:
        by_level.setdefault(step["level"], []).append(step["seconds"])

    print("\n" + "=" * 78)
    print(f"LATENCY — {len(results)} facts, {len(hops)} LLM calls")
    print("=" * 78)
    for level in ("domain", "section", "leaf"):
        times = sorted(by_level.get(level, []))
        if not times:
            continue
        print(f"  hop {level:<8} n={len(times):<4} p50={times[len(times) // 2]:5.2f}s  "
              f"p90={times[int(len(times) * 0.9) - 1]:5.2f}s")
    per_fact.sort()
    print(f"  per fact (3 hops, sequential): p50={per_fact[len(per_fact) // 2]:.1f}s  "
          f"p90={per_fact[int(len(per_fact) * 0.9) - 1]:.1f}s  "
          f"mean={statistics.mean(per_fact):.1f}s")
    print(f"  a 150-fact document at {MAX_WORKERS} workers: "
          f"~{statistics.mean(per_fact) * 150 / MAX_WORKERS / 60:.1f} min")

    if real_facts:
        committed = sum(
            1 for f in real_facts
            if results.get(f) and results[f]["leaf"] not in (None, NONE_FIT)
        )
        refused = len(real_facts) - committed
        domains = Counter(
            results[f]["domain"] for f in real_facts if results.get(f)
        )
        print("\n" + "=" * 78)
        print(f"REAL-DOCUMENT FACTS (n={len(real_facts)}) — behaviour only")
        print("=" * 78)
        print(f"  committed to a leaf : {committed}   refused/failed: {refused}")
        print(f"  domains chosen      : {dict(domains.most_common(6))}")
        print("  PRECISION NOT REPORTED — evaluation/real_answer_key_sample.csv")
        print("  has no correct_node_id filled in, so there is nothing to score")
        print("  these against. Label it and this becomes measurable.")

    print(f"\nper-fact detail: {OUT}")
    print("Nothing written to knowledge_base or bp_architecture.")


if __name__ == "__main__":
    main()
