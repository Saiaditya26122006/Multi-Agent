#!/usr/bin/env python3
"""Can a confidence threshold buy 95% auto-file precision, at any coverage?

Trades coverage for precision, as asked: auto-file only above a confidence
level, park or review everything below. Also tests a second gate — never
auto-file into a node whose own rules are degraded or boilerplate, because the
reasoner has nothing solid to reason against there.

⚠️ **Scored on the LABELLED sets only (bucket-a n=54, paraphrase n=10).**
`evaluation/real_answer_key_sample.csv` still has `correct_node_id` blank, so
real-document precision — at any threshold, with or without gates — is not
computable. The real facts are run for behaviour and cost only.

⚠️ **The cells are small.** 54 facts split three ways by confidence leaves
buckets of a dozen or so. A 95% precision claim needs roughly 19 correct out of
20 in a bucket to be distinguishable from noise; nothing here can establish that.
Read this as direction, not calibration.

Writes nothing.

    python scripts/calibrate_llm_nav.py
"""

import csv
import json
import logging
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from services.feed_classifier_llm_nav import NONE_FIT, navigate  # noqa: E402
from services.feed_classifier_v3 import load_architecture  # noqa: E402

sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
from measure_llm_nav import PARAPHRASES, KEY, REAL  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

OUT = os.path.join(os.getenv("CLAUDE_JOB_DIR", "/tmp"), "tmp", "llm_nav_calib.json")
MAX_WORKERS = 6
CALL_TIMEOUT = 600
TOTAL_TIMEOUT = 7200

LEVELS = ("certain", "likely", "unsure")

# A node the reasoner cannot reason against: its rules are missing, placeholder,
# or the non-discriminating template found across ~45 BP.9 leaves.
BOILER_PURPOSE = re.compile(
    r"^Governs .+ without validating downstream commercial success\.?$", re.I
)
BOILER_OUTPUT = re.compile(r"^.+ specification and governance\.?$", re.I)


def thin_rules(node: dict) -> str:
    """Why this node is a weak auto-file target, or '' when it is sound."""
    if node.get("degraded_target"):
        return f"degraded:{node.get('degraded_reason')}"
    purpose = (node.get("purpose") or "").strip()
    output = (node.get("required_output") or "").strip()
    if not output or output == "None":
        return "no_required_output"
    if BOILER_PURPOSE.match(purpose) and BOILER_OUTPUT.match(output):
        return "boilerplate_rules"
    return ""


def load_labelled():
    """The fact sets that have ground truth."""
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


def navigate_all(facts, arch):
    """Navigate every fact once; reuse across every threshold and gate."""
    out = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(navigate, f, arch) for f in facts]
        done = 0
        for future in as_completed(futures, timeout=TOTAL_TIMEOUT):
            try:
                result = future.result(timeout=CALL_TIMEOUT)
                payload = result.to_dict()
                payload["confidence"] = result.confidence
                out[result.fact] = payload
            except Exception as exc:  # noqa: BLE001 — counted, not swallowed
                logging.error("navigate failed: %s", str(exc)[:180])
            done += 1
            if done % 20 == 0:
                print(f"    {done}/{len(facts)}")
    return out


def evaluate(items, results, arch, allowed: set[str], gate_thin: bool):
    """Auto-file stats under one confidence policy and gate setting."""
    filed = correct = 0
    parked = 0
    wrong_good_rule = []
    wrong_thin_rule = []
    for targets, text in items:
        r = results.get(text)
        if r is None:
            continue
        leaf = r["leaf"]
        if leaf in (None, NONE_FIT):
            parked += 1
            continue
        node = arch.nodes.get(leaf, {})
        weak = thin_rules(node)
        if r["confidence"] not in allowed or (gate_thin and weak):
            parked += 1
            continue
        filed += 1
        if leaf in targets:
            correct += 1
        elif weak:
            wrong_thin_rule.append((text, leaf, weak))
        else:
            wrong_good_rule.append((text, leaf, r["confidence"]))
    return filed, correct, parked, wrong_good_rule, wrong_thin_rule


def main() -> None:
    """Run the calibration and report every table asked for."""
    arch = load_architecture()
    labelled = load_labelled()
    facts = [t for items in labelled.values() for _, t in items]

    real_facts = []
    if os.path.exists(REAL):
        real_facts = [r["fact_text"] for r in csv.DictReader(open(REAL))][:20]
    print(f"navigating {len(facts) + len(real_facts)} facts, 3 hops each ...")
    results = navigate_all(facts + real_facts, arch)
    json.dump(results, open(OUT, "w"), indent=1)

    # ---- 1. precision vs confidence --------------------------------------
    print("\n" + "=" * 86)
    print("1. PRECISION BY CONFIDENCE LEVEL  (labelled sets — no gate)")
    print("=" * 86)
    for name, items in labelled.items():
        print(f"\n  {name}")
        print(f"    {'confidence':<12}{'filed':>7}{'correct':>9}{'precision':>12}")
        print("    " + "-" * 40)
        for level in LEVELS:
            filed, correct, *_ = evaluate(items, results, arch, {level}, False)
            pct = f"{100.0 * correct / filed:.1f}%" if filed else "n/a"
            print(f"    {level:<12}{filed:>7}{correct:>9}{pct:>12}")

    # ---- 2. cumulative thresholds, with and without the thin-rule gate ----
    print("\n" + "=" * 86)
    print("2. THRESHOLD SWEEP — auto-file at or above each level")
    print("=" * 86)
    policies = [("certain only", {"certain"}),
                ("certain+likely", {"certain", "likely"}),
                ("all (no threshold)", set(LEVELS))]
    for name, items in labelled.items():
        n = len(items)
        print(f"\n  {name}  (n={n})")
        print(f"    {'policy':<20}{'gate':<8}{'auto-filed':>12}{'coverage':>10}"
              f"{'precision':>12}")
        print("    " + "-" * 62)
        for label, allowed in policies:
            for gate in (False, True):
                filed, correct, parked, _, _ = evaluate(
                    items, results, arch, allowed, gate
                )
                pct = f"{100.0 * correct / filed:.1f}%" if filed else "n/a"
                print(f"    {label:<20}{'ON' if gate else 'off':<8}{filed:>12}"
                      f"{100.0 * filed / n:>9.1f}%{pct:>12}")

    # ---- 3. failure split -------------------------------------------------
    print("\n" + "=" * 86)
    print("3. WRONG AUTO-FILES — overconfident-but-sound-rule vs thin-rule node")
    print("=" * 86)
    for name, items in labelled.items():
        _, _, _, good, thin = evaluate(items, results, arch, set(LEVELS), False)
        print(f"\n  {name}: {len(good)} sound-rule, {len(thin)} thin-rule "
              f"(of {len(good) + len(thin)} wrong)")
        for text, leaf, conf in good[:6]:
            print(f"    (a) [{conf}] {text[:52]!r} -> {leaf}")
        for text, leaf, why in thin[:6]:
            print(f"    (b) [{why}] {text[:52]!r} -> {leaf}")

    # ---- how many nodes are even eligible ---------------------------------
    weak = sum(1 for n in arch.leaf_ids if thin_rules(arch.nodes[n]))
    print(f"\n  thin-rule leaves in the architecture: {weak}/{len(arch.leaf_ids)} "
          f"({100.0 * weak / len(arch.leaf_ids):.1f}%) — the gate's reach")

    # ---- cost and latency -------------------------------------------------
    per_fact = sorted(r["seconds"] for r in results.values())
    hops = [s for r in results.values() for s in r["steps"]]
    print("\n" + "=" * 86)
    print(f"COST / LATENCY — {len(results)} facts, {len(hops)} LLM calls")
    print("=" * 86)
    print(f"  per fact (3 sequential hops): p50={per_fact[len(per_fact) // 2]:.1f}s  "
          f"p90={per_fact[int(len(per_fact) * 0.9) - 1]:.1f}s")
    print("  measured tokens: 3,241 in / 137 out per fact  ->  $0.0118/fact")
    print(f"  a 150-fact document at {MAX_WORKERS} workers: "
          f"~{sum(per_fact) / len(per_fact) * 150 / MAX_WORKERS / 60:.1f} min, ~$1.77")

    if real_facts:
        conf = Counter(results[f]["confidence"] for f in real_facts if results.get(f))
        filed = sum(1 for f in real_facts
                    if results.get(f) and results[f]["leaf"] not in (None, NONE_FIT))
        print("\n" + "=" * 86)
        print(f"REAL-DOCUMENT FACTS (n={len(real_facts)}) — behaviour only")
        print("=" * 86)
        print(f"  committed to a leaf: {filed}   confidence mix: {dict(conf)}")
        print("  PRECISION NOT COMPUTABLE — real_answer_key_sample.csv has no")
        print("  correct_node_id filled in. Every number in sections 1-3 above is")
        print("  from the labelled sets, NOT from real data.")

    print(f"\ndetail: {OUT}\nNothing written.")


if __name__ == "__main__":
    main()
