#!/usr/bin/env python3
"""Three-way comparison of the 27-card reference paste: baseline / control / fixed.

Answers the only question a two-way diff cannot: did a fix change this card, or
would the run have changed it anyway? The control arm is the current code with
dedupe, grouping and sibling context all switched off, run on the same text, so a
card that moves in the control moved on its own.

    python scripts/rerun_feed_27.py --control   # writes evaluation/feed_27_control.json
    python scripts/rerun_feed_27.py             # writes evaluation/feed_27_rerun.json
    python scripts/compare_feed_27_arms.py
"""

import json
import os
import sys
from typing import Any, Optional

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from services import feed_batch_store  # noqa: E402
from scripts.rerun_feed_27 import BASELINE_RUN, match_new, node_of  # noqa: E402


def load(name: str) -> list[dict[str, Any]]:
    """Load one arm's cards from evaluation/<name>."""
    path = os.path.join(PROJECT_ROOT, "evaluation", name)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)["cards"]


def main() -> None:
    """Print the three-way table and the attribution summary."""
    baseline = feed_batch_store.get_batch(BASELINE_RUN)
    if not baseline:
        raise SystemExit(f"baseline batch {BASELINE_RUN} not found")
    baseline_cards = baseline["cards"]
    control = load("feed_27_control.json")
    fixed = load("feed_27_rerun.json")

    print("card  baseline      control       fixed         attribution")
    counts = {"fix-caused": 0, "drift": 0, "unchanged": 0}
    rows = []
    for origin in sorted(baseline_cards, key=lambda c: c["index"]):
        number = origin["index"] + 1
        c_card = match_new(origin, control)
        f_card = match_new(origin, fixed)
        base_node, c_node, f_node = node_of(origin), node_of(c_card), node_of(f_card)

        if f_node == c_node:
            attribution = "unchanged" if f_node == base_node else "drift"
        else:
            attribution = "fix-caused"
        counts[attribution] += 1
        rows.append((number, base_node, c_node, f_node, attribution, origin["fact"]))
        print(
            "  %02d  %-12s  %-12s  %-12s  %s"
            % (number, base_node, c_node, f_node, attribution)
        )

    print("\nattribution: %s" % ", ".join(f"{k}={v}" for k, v in counts.items()))

    print("\nfix-caused changes (control -> fixed):")
    for number, base_node, c_node, f_node, attribution, fact in rows:
        if attribution == "fix-caused":
            print("  %02d  %-12s -> %-12s  %s" % (number, c_node, f_node, fact[:58]))

    print("\ndrift (control already differs from baseline, fixes not involved):")
    for number, base_node, c_node, f_node, attribution, fact in rows:
        if attribution == "drift":
            print("  %02d  %-12s -> %-12s  %s" % (number, base_node, c_node, fact[:58]))


if __name__ == "__main__":
    main()
