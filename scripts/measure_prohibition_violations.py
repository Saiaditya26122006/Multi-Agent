#!/usr/bin/env python3
"""How often does the pipeline propose a node whose prohibitions the fact violates?

The question that decides whether a prohibition gate is worth building.
`prohibited_claims_inference_patterns` is currently advisory — it is rendered
into the judge prompt and nothing checks the result. 834 of 912 nodes carry one,
799 of them distinct. Whether that matters depends entirely on a number nobody
has measured: the violation rate at filing time.

This replays ALREADY RECORDED proposals. It runs no classification and changes no
pipeline — for each (fact, proposed node) pair it asks once whether the fact
asserts what that node forbids. So the measurement is of the pipeline as it
shipped, not of a pipeline built to be measured.

Judged one pair per call, reason before verdict, because a batched judge that
sees twenty pairs at once anchors on the first few.

    python scripts/measure_prohibition_violations.py
    python scripts/measure_prohibition_violations.py --limit 20

Writes evaluation/prohibition_violations.json and prints the rate.
"""

import argparse
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

from services.feed_classifier_v3 import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_MODEL_ENV,
    _call_llm,
    _parse_json_object,
    load_architecture,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

RECORDED = ("feed_27_rerun.json", "feed_27_control.json", "feed_27_baseline.json")
OUT = os.path.join(PROJECT_ROOT, "evaluation", "prohibition_violations.json")
MAX_WORKERS = 6

VIOLATION_PROMPT = """You check one fact against one node's prohibition rule.

The node carries a rule saying which inferences must NOT be made at it. Decide
whether filing this fact here would assert what the rule forbids.

A violation is the fact ASSERTING the forbidden inference. It is not:
  - the fact being about the same topic as the rule
  - the fact being weak, vague or unsupported
  - the fact mentioning a word the rule also mentions
  - the fact being a poor fit for the node

The rules are written as "Must not infer X from Y" or "Must not treat X as Y".
The fact violates it only when the fact actually does that inference.

  rule  "Must not infer buyer authority from user pain or stakeholder enthusiasm."
  fact  "Researchers are frustrated by desk rejections."          -> NO
        (states the pain; infers no authority from it)
  fact  "Researchers are frustrated, so they are the buyers."     -> YES
        (infers buyer identity from user pain — exactly the forbidden move)

Return one JSON object, nothing else:

{"reason": "<one sentence: what the fact asserts, and whether that is the move
            the rule forbids>",
 "violates": true or false}

Write "reason" first and let "violates" follow from it. A verdict that
contradicts its own reason makes both untrustworthy. When in doubt, answer
false — a false positive here would block a correct filing."""


def load_pairs(limit: Optional[int]) -> list[dict[str, Any]]:
    """Every (fact, proposed node) pair in the recorded runs, de-duplicated."""
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for name in RECORDED:
        path = os.path.join(PROJECT_ROOT, "evaluation", name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            batch = json.load(handle)
        for card in batch.get("cards", []):
            fact = card.get("fact") or card.get("text") or ""
            node = card.get("confirmed_node_id") or card.get("proposed_node_id")
            if not fact or not node:
                continue
            pairs.setdefault((fact, node), {"fact": fact, "node_id": node, "runs": []})
            pairs[(fact, node)]["runs"].append(name)
    out = list(pairs.values())
    return out[:limit] if limit else out


def judge(pair: dict[str, Any], arch: Any, model: str) -> dict[str, Any]:
    """Ask whether one fact violates one node's prohibition rule."""
    node = arch.nodes.get(pair["node_id"])
    rule = (node or {}).get("prohibited_claims_inference_patterns") or ""
    result = {**pair, "rule": rule.strip(), "title": (node or {}).get("node_title")}
    if not node:
        return {**result, "skipped": "node not in architecture"}
    if not rule.strip():
        return {**result, "skipped": "node has no prohibition rule"}

    payload = (
        f"NODE: {pair['node_id']} — {node.get('node_title')}\n"
        f"PROHIBITION RULE: {rule.strip()}\n\n"
        f"FACT: {pair['fact']}"
    )
    try:
        parsed = _parse_json_object(_call_llm(VIOLATION_PROMPT, payload, model))
    except Exception as exc:  # noqa: BLE001 — recorded, not silently dropped
        logger.error("check failed for %s: %s", pair["node_id"], str(exc)[:160])
        return {**result, "error": str(exc)[:200]}
    return {
        **result,
        "violates": bool(parsed.get("violates")),
        "reason": str(parsed.get("reason", "")).strip(),
    }


def main() -> None:
    """Measure and report the violation rate over the recorded proposals."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    arch = load_architecture()
    pairs = load_pairs(args.limit)
    model = os.getenv(DEFAULT_MODEL_ENV, DEFAULT_MODEL)
    print(f"{len(pairs)} distinct (fact, proposed node) pair(s) from recorded runs")

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(judge, p, arch, model): p for p in pairs}
        for done in as_completed(futures):
            results.append(done.result())

    checked = [r for r in results if "violates" in r]
    violations = [r for r in checked if r["violates"]]
    skipped = [r for r in results if "skipped" in r]
    errors = [r for r in results if "error" in r]

    print()
    print("=" * 74)
    print(f"pairs checked        : {len(checked)}")
    print(f"no prohibition rule  : {len(skipped)}")
    print(f"errors               : {len(errors)}")
    print(f"VIOLATIONS           : {len(violations)}")
    if checked:
        print(f"violation rate       : {len(violations) / len(checked) * 100:.1f}%")
    print("=" * 74)
    for v in violations:
        print(f"\n  {v['node_id']} — {v['title']}")
        print(f"    rule : {v['rule'][:150]}")
        print(f"    fact : {v['fact'][:150]}")
        print(f"    why  : {v['reason'][:200]}")

    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "checked": len(checked),
                "violations": len(violations),
                "rate": (len(violations) / len(checked)) if checked else None,
                "results": results,
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
