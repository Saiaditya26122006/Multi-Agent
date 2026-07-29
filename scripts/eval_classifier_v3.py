#!/usr/bin/env python3
"""Measure Feed classifier v3 — section-first retrieval + sibling judge.

Answers the one question Alex's 95% bar turns on: **when the judge commits to an
auto-file, how often is it right?** Precision and coverage are reported
separately and never averaged, because they move in opposite directions here —
section recall is measured at 38.9% @1 / 20.0% @1 on paraphrases, so coverage is
expected to be low. Precision is what is actually unknown.

Four fact sets, scored apart:

    textbook    54 bucket-a clear + hard facts, phrased as the key wrote them
    paraphrase  10 oblique/messy restatements — the real number
    boundary    9 facts with two defensible homes (A|B), scored as either
    degraded    5 facts whose target is a degraded node — must be 5/5 review

Two retrieval configurations are run over every fact so the margin gate is set
FROM the measurement rather than guessed:

    top1   commit to the highest-ranked section
    top2   judge across both sibling sets (the section-miss recovery path)

A margin-gated hybrid (use top1 when the section margin is wide, top2 when it is
narrow) is then swept offline over the recorded outcomes at no extra LLM cost.

Writes nothing.

    python scripts/eval_classifier_v3.py
"""

import csv
import json
import logging
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from supabase import create_client  # noqa: E402

from services.embedding_service import embed  # noqa: E402
from services.feed_classifier_v3 import (  # noqa: E402
    AUTO_FILE,
    PARENT_PARKED,
    REVIEW,
    classify,
    load_architecture,
    parent_of,
)

logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")

KEY = os.path.join(PROJECT_ROOT, "evaluation", "classifier_answer_key_draft.csv")
OUT = os.path.join(os.getenv("CLAUDE_JOB_DIR", "/tmp"), "tmp", "eval_v3_results.json")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

MAX_WORKERS = 8
CALL_TIMEOUT = 180
TOTAL_TIMEOUT = 3600

# The margin at which the swept hybrid peaked on the FIRST run of this eval.
# Fit on the same 78 facts it is scored against, so it is optimistic — a real
# threshold needs a held-out set. Recorded here rather than hidden in a default.
MEASURED_MARGIN_GATE = 0.037

# Configurations run live against Bedrock. opt1/opt2 are derived offline from
# top1/top2 instead, because both optimisations only remove a judge call or
# choose between two already-recorded ones — the derivation is exact.
LIVE_CONFIGS = {
    "top1": dict(sections_to_consider=1),
    "top2": dict(sections_to_consider=2),
    "opt12": dict(
        margin_gate=MEASURED_MARGIN_GATE, skip_judge_single_sibling=True
    ),
}

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


def load_facts() -> list[dict]:
    """Assemble the four labelled fact sets from the key plus the paraphrases."""
    key = list(csv.DictReader(open(KEY)))
    facts = []
    for row in key:
        if row["scoring_bucket"] != "a_classifier":
            continue
        category = row["category"]
        group = {"clear": "textbook", "hard": "textbook"}.get(category, category)
        facts.append(
            {
                "group": group,
                "targets": row["correct_node_id"].split("|"),
                "text": row["fact_text"],
            }
        )
    for target, text in PARAPHRASES:
        facts.append({"group": "paraphrase", "targets": [target], "text": text})
    return facts


def run_one(item: tuple[int, dict, str], arch) -> tuple[int, str, dict]:
    """Classify one fact under one named configuration."""
    idx, fact, config = item
    routing = classify(
        fact["text"], arch, fact_vector=fact["vector"], **LIVE_CONFIGS[config]
    )
    return idx, config, routing.to_dict()


def derive_opt1(results: dict, degraded_ids: set[str]) -> dict[int, dict]:
    """OPT-1 applied to top-1: single-sibling sections skip the judge.

    Exact, not simulated: with one candidate the judge's only remaining job is
    the fit check, and skipping it forces an auto-file to that node. Degraded
    nodes still route to review — the contract does not bend for an optimisation.
    """
    out = {}
    for idx, routing in results["top1"].items():
        candidates = routing["candidate_leaf_ids"]
        if len(candidates) != 1 or candidates[0] in degraded_ids:
            out[idx] = routing
            continue
        only = candidates[0]
        forced = dict(routing)
        forced.update(
            judge_choice=only,
            judge_confidence="high",
            decision=AUTO_FILE,
            target_node_id=only,
            reason="OPT-1: single-sibling section, judge skipped",
        )
        out[idx] = forced
    return out


def derive_opt2(results: dict, gate: float) -> dict[int, dict]:
    """OPT-2: commit to the top section when the margin is wide, else widen."""
    out = {}
    for idx in results["top1"]:
        if idx not in results["top2"]:
            out[idx] = results["top1"][idx]
            continue
        margin = results["top1"][idx]["section_margin"] or 0.0
        out[idx] = results["top1"][idx] if margin >= gate else results["top2"][idx]
    return out


def pct(numerator: int, denominator: int) -> str:
    """Format a percentage, or n/a when the denominator is zero."""
    return f"{100.0 * numerator / denominator:.1f}%" if denominator else "n/a"


def score(facts: list[dict], results: dict, config: str, group: str) -> dict:
    """Score one group under one configuration."""
    rows = [
        (f, results[config][i])
        for i, f in enumerate(facts)
        if f["group"] == group and i in results[config]
    ]
    stats = Counter()
    wrong_auto = []
    for fact, routing in rows:
        stats["n"] += 1
        decision = routing["decision"]
        stats[decision] += 1
        correct_leaf = routing["target_node_id"] in fact["targets"]
        section_hit = any(
            s["section_id"] in {parent_of(t) for t in fact["targets"]}
            for s in routing["sections"][: int(routing["thresholds_provisional"][
                "sections_to_consider"])]
        )
        if section_hit:
            stats["section_hit"] += 1
        if decision == AUTO_FILE:
            if correct_leaf:
                stats["auto_correct"] += 1
            else:
                wrong_auto.append((fact, routing))
        # Leaf accuracy is the judge's pick, independent of how it was routed:
        # a correct leaf held back at low confidence is still a correct pick.
        if routing["judge_choice"] in fact["targets"]:
            stats["leaf_correct"] += 1
        if decision == PARENT_PARKED and routing["target_node_id"] in {
            parent_of(t) for t in fact["targets"]
        }:
            stats["park_correct"] += 1
    return {"stats": stats, "wrong_auto": wrong_auto, "rows": rows}


def main() -> None:
    """Run both configurations over every fact and report precision vs coverage."""
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    arch = load_architecture(sb)
    sizes = sorted(len(v) for v in arch.siblings.values())
    print(
        f"architecture: {len(arch.leaf_ids)} embedded leaves, "
        f"{len(arch.siblings)} sibling groups "
        f"(min={sizes[0]} median={sizes[len(sizes) // 2]} max={sizes[-1]})"
    )

    facts = load_facts()
    print(f"facts: {Counter(f['group'] for f in facts)}")
    print("embedding ...")
    for fact in facts:
        fact["vector"] = embed(fact["text"], input_type="search_query")

    jobs = [(i, f, c) for c in LIVE_CONFIGS for i, f in enumerate(facts)]
    print(f"running {len(jobs)} judge calls at {MAX_WORKERS} workers ...\n")

    results: dict[str, dict[int, dict]] = {c: {} for c in LIVE_CONFIGS}
    failures = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(run_one, job, arch): job for job in jobs}
        done = 0
        for future in as_completed(futures, timeout=TOTAL_TIMEOUT):
            try:
                idx, config, routing = future.result(timeout=CALL_TIMEOUT)
                results[config][idx] = routing
            except Exception as exc:  # noqa: BLE001 — counted and reported
                failures += 1
                logging.error("judge call failed: %s", str(exc)[:200])
            done += 1
            if done % 30 == 0:
                print(f"  {done}/{len(jobs)}")

    degraded_ids = {
        n for n, r in arch.nodes.items() if r.get("degraded_target")
    }
    results["opt1"] = derive_opt1(results, degraded_ids)
    results["opt2"] = derive_opt2(results, MEASURED_MARGIN_GATE)

    if failures:
        print(f"\n!! {failures} judge calls failed and are excluded from the counts")

    with open(OUT, "w") as handle:
        json.dump(
            {
                "facts": [{k: v for k, v in f.items() if k != "vector"} for f in facts],
                "results": results,
            },
            handle,
            indent=1,
        )

    groups = ["textbook", "paraphrase", "boundary", "degraded"]
    labels = {
        "top1": "BASELINE — commit to top section",
        "top2": "BASELINE — judge across top-2 sections",
        "opt1": "OPT-1 only — top1 + skip judge on single-sibling sections",
        "opt2": f"OPT-2 only — margin-gated top1/top2 at {MEASURED_MARGIN_GATE}",
        "opt12": "OPT-1 + OPT-2 (live run)",
    }
    for config in ("top1", "top2", "opt1", "opt2", "opt12"):
        print("\n" + "=" * 78)
        print(f"CONFIG {config} — {labels[config]}")
        print("=" * 78)
        header = (
            f"{'group':<12}{'n':>4}{'sect':>7}{'auto':>7}{'park':>7}{'revw':>7}"
            f"{'AUTO-FILE PREC':>16}{'leaf acc':>10}"
        )
        print(header)
        print("-" * len(header))
        for group in groups:
            scored = score(facts, results, config, group)
            s = scored["stats"]
            n = s["n"]
            auto = s[AUTO_FILE]
            print(
                f"{group:<12}{n:>4}"
                f"{pct(s['section_hit'], n):>7}"
                f"{auto:>7}{s[PARENT_PARKED]:>7}{s[REVIEW]:>7}"
                f"{pct(s['auto_correct'], auto):>16}"
                f"{pct(s['leaf_correct'], n):>10}"
            )

    # ---- the decisive cell, plus what the auto-file errors actually are ----
    print("\n" + "=" * 78)
    print("VERDICT — did either optimisation move auto-file precision down?")
    print("=" * 78)
    print(f"{'config':<8}{'judge calls':>13}{'auto':>7}{'PRECISION (all)':>18}"
          f"{'para auto':>11}{'para prec':>11}")
    print("-" * 68)
    for config in ("top1", "top2", "opt1", "opt2", "opt12"):
        auto = correct = pauto = pcorrect = calls = 0
        for idx, routing in results[config].items():
            fact = facts[int(idx)]
            skipped = routing["reason"].startswith(("OPT-1", "single-sibling"))
            if routing["candidate_leaf_ids"] and not skipped:
                calls += 1
            if routing["decision"] != AUTO_FILE:
                continue
            hit = routing["target_node_id"] in fact["targets"]
            auto += 1
            correct += hit
            if fact["group"] == "paraphrase":
                pauto += 1
                pcorrect += hit
        print(f"{config:<8}{calls:>13}{auto:>7}{pct(correct, auto):>18}"
              f"{pauto:>11}{pct(pcorrect, pauto):>11}")

    print("\n" + "=" * 78)
    print("AUTO-FILE ERRORS — every fact the judge committed to and got wrong")
    print("=" * 78)
    for config in ("top1", "top2"):
        for group in groups:
            for fact, routing in score(facts, results, config, group)["wrong_auto"]:
                print(f"\n  [{config}/{group}] {fact['text'][:64]!r}")
                print(f"    correct : {'|'.join(fact['targets'])}")
                print(f"    filed   : {routing['target_node_id']}")
                print(f"    in set  : {routing['target_node_id'] in routing['candidate_leaf_ids']}"
                      f"  correct in set: "
                      f"{any(t in routing['candidate_leaf_ids'] for t in fact['targets'])}")
                print(f"    judge   : {routing['judge_reason'][:200]}")

    # ---- margin-gated hybrid, swept offline over the recorded outcomes ----
    print("\n" + "=" * 78)
    print("MARGIN-GATED HYBRID — top1 when margin >= t, else top2 (swept, not tuned)")
    print("=" * 78)
    margins = sorted(
        {
            round(r["section_margin"], 3)
            for r in results["top1"].values()
            if r["section_margin"] is not None
        }
    )
    grid = [0.0] + margins[:: max(len(margins) // 12, 1)] + [1.0]
    print(f"{'t':>7}{'auto':>7}{'prec':>9}{'auto(par)':>11}{'prec(par)':>11}")
    print("-" * 45)
    for t in grid:
        auto = correct = pauto = pcorrect = 0
        for i, fact in enumerate(facts):
            if i not in results["top1"] or i not in results["top2"]:
                continue
            margin = results["top1"][i]["section_margin"]
            routing = results["top1"][i] if (margin or 0) >= t else results["top2"][i]
            if routing["decision"] != AUTO_FILE:
                continue
            hit = routing["target_node_id"] in fact["targets"]
            auto += 1
            correct += hit
            if fact["group"] == "paraphrase":
                pauto += 1
                pcorrect += hit
        print(
            f"{t:>7.3f}{auto:>7}{pct(correct, auto):>9}"
            f"{pauto:>11}{pct(pcorrect, pauto):>11}"
        )

    print(f"\nper-fact detail written to {OUT}")
    print("Nothing written to knowledge_base or bp_architecture.")


if __name__ == "__main__":
    main()
